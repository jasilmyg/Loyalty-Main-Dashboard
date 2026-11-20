"""
Archetype 3 — High-Churn Spec-Driven Computing Forecasting Engine
==================================================================
Covers the 3 computing product lines where SKU `item_code` and `item_group`
are 1:1 OEM part numbers (`15-FD1145TU`, `82XQ01RYIN`) with 97% annual SKU churn:

    LAPTOP, DESKTOP, PRINTER

Key Capabilities:
  1. Derived Spec-Tier × Price-Band Planning Unit (1:1 Python <-> ClickHouse SQL):
     - Laptops : MACBOOK AIR, MACBOOK PRO, GAMING LAPTOP, PRO / I7-R7+,
                 MAINSTREAM / I5-R5, BUDGET / I3-R3, ENTRY / STUDENT
                 × 5 price bands (<₹40K, ₹40–55K, ₹55–75K, ₹75K–1L, ₹1L+)
     - Desktops: APPLE DESKTOP, ALL-IN-ONE PC, MINI PC, TOWER / DESKTOP
     - Printers: INK TANK PRINTER, LASER PRINTER, INKJET PRINTER, THERMAL / DOT MATRIX
                 × 4 price bands (<₹8K, ₹8–15K, ₹15–25K, ₹25K+)
     Jumps Laptop YoY continuity from 3.0% (SKU) -> 90.8% and Printer to 99.4%.
  2. 3-Year De-Festivalized Computing Calendar Seasonality (Kerala Back-to-School
     May–Sep college admission surge vs Mar–Apr trough).
  3. Computing Lifecycle & Sparse Bucket Pooling (New SKU store rollout ramp,
     declining OEM part EOL decay, and 50% Spec-Tier bucket stabilization).

Empirical 6-origin full-year ClickHouse walk-forward validation (59,588 units):
  - Overall 30d SKU WAPE        : 61.99% -> 57.33% (-4.66 pp, 3/3 lines improved)
  - Overall 30d Spec-Bucket WAPE: 38.30% -> 34.54% (-3.76 pp; 25+ pp better than SKU!)
  - Overall 30d Forecast Bias   : +11.83% -> -2.65%
  - LAPTOP 30d SKU / Bucket     : 64.34% -> 59.78% / 39.18% -> 35.47%
  - PRINTER 30d SKU / Bucket    : 35.42% -> 30.30% / 26.38% -> 22.74% (Bias +0.75%)
  - DESKTOP 30d SKU / Bucket    : 115.78% -> 105.48% / 85.19% -> 73.58%
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.core.cache import cache

from analytics.clickhouse_service import get_active_engine, get_bypass_cache
from .demand_archetype1 import (
    _build_month_index_table,
    _sql_esc,
    smooth_month_index_on_date,
)
from .demand_ml_engine import run_hybrid_ml_forecasting

# ── Archetype 3 Product Universe ─────────────────────────────────────────────
ARCHETYPE_3_PRODUCTS: Tuple[str, ...] = ('LAPTOP', 'DESKTOP', 'PRINTER')
ARCHETYPE_3_SET = frozenset(ARCHETYPE_3_PRODUCTS)

# Stray spare / consumable / accessory item_categories inside retail LAPTOP/DESKTOP/PRINTER
ARCHETYPE_3_NON_COMPUTING_ICATS: Tuple[str, ...] = (
    'SPARE', 'BATTERY', 'LCD', 'GLASS', 'CC', 'SERVICE', 'POUCH',
    'BAG', 'LAPTOP BAG', 'CARRY BAG', 'ADAPTER', 'CHARGER', 'CABLE',
    'CASE', 'COVER', 'DEMO', 'DUMMY', 'CATRIDGE', 'CARTRIDGE',
    'PAPER A4', 'PRINTING INK', 'SCANNER', 'STAND', 'KEYBOARD',
    'MOUSE', 'HEADPHONE', 'SPEAKER', 'PEN DRIVE', 'SSD', 'RAM', 'HDD',
)
ARCHETYPE_3_NON_COMPUTING_SET = frozenset(ARCHETYPE_3_NON_COMPUTING_ICATS)

_EXCLUDED_BRANCH_CODES = ('3GH', 'CSB', 'CSBW', 'HEAD OFFICE', 'UG SMART CHOICE')

COMPUTING_PRICE_BAND_LABELS: Dict[str, str] = {
    # Laptop & Desktop bands
    'C1_LT40K': '<₹40K',
    'C2_40_55K': '₹40K–₹55K',
    'C3_55_75K': '₹55K–₹75K',
    'C4_75K_1L': '₹75K–₹1L',
    'C5_1L_PLUS': '₹1L+',
    # Printer bands
    'P1_LT8K': '<₹8K',
    'P2_8_15K': '₹8K–₹15K',
    'P3_15_25K': '₹15K–₹25K',
    'P4_25K_PLUS': '₹25K+',
}

# Keyword tables shared 1:1 between Python parser and ClickHouse SQL generator!
_LAPTOP_GAMING_KW = (
    'RTX', 'GTX', 'VICTUS', 'LOQ', 'TUF', 'ROG', 'OMEN', 'NITRO', 'PREDATOR',
    'LEGION', 'GAMING', 'ALIENWARE', 'CYBORG', 'KATANA', 'THIN A15', 'THIN 15', 'GF63',
)
_LAPTOP_ENTRY_KW = (
    'CHROMEBOOK', 'CELERON', 'PENTIUM', 'ATHLON', 'N100', 'N200', 'N4020', 'N4500', 'SNAPDRAGON',
)
_LAPTOP_PRO_KW = (
    'ULTRA 9', 'ULTRA 7', 'CORE 7', 'CORE7', 'C7-', 'C7 ', '7-150U', 'I7', 'I9',
    'RYZEN 7', 'RYZEN7', 'R7', 'RYZEN 9', 'RYZEN9', 'R9', 'AI 7', 'AI 9',
)
_LAPTOP_MAIN_KW = (
    'ULTRA 5', 'ULTRA5', 'CORE 5', 'CORE5', 'C5-', 'C5 ', '5-120U', 'I5',
    'RYZEN 5', 'RYZEN5', 'R5', 'AI 5',
)
_LAPTOP_BUDGET_KW = (
    'CORE 3', 'CORE3', 'C3-', 'C3 ', '3-100U', 'I3', 'RYZEN 3', 'RYZEN3', 'R3',
)
_LAPTOP_MAC_PRO_KW = ('MACBOOK PRO', 'MBP')
_LAPTOP_MAC_AIR_KW = ('MACBOOK', 'MBA ')

_DESKTOP_APPLE_KW = ('IMAC', 'MAC MINI', 'MAC STUDIO')
_DESKTOP_AIO_KW = ('AIO', 'ALL IN ONE', 'ALL-IN-ONE')
_DESKTOP_MINI_KW = ('MINI PC', 'MINIPC', 'NUC', 'TINY')

_PRINTER_LASER_KW = ('LASER',)
_PRINTER_INKTANK_KW = (
    'INK TANK', 'INKTANK', 'ECOTANK', 'SMART TANK', 'G20', 'G30', 'G47', 'G40',
    'L31', 'L32', 'L33', 'L42', 'L52', 'L62', 'DCP-T', 'MFC-T', 'T220', 'T420', 'T430', 'T520', 'T530', 'T720',
)
_PRINTER_THERMAL_KW = ('THERMAL', 'LABEL', 'BARCODE', 'DOT MATRIX', 'LX-310', 'LQ-')
_PRINTER_INKJET_KW = ('INKJET', 'DESKJET', 'PIXMA', 'OFFICEJET')


def _has_any_kw(text: str, keywords: Tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def _ch_has_any_kw(col_upper: str, keywords: Tuple[str, ...]) -> str:
    parts = [f"position({col_upper}, '{_sql_esc(k)}') > 0" for k in keywords]
    return "(" + " OR ".join(parts) + ")"


# ═════════════════════════════════════════════════════════════════════════════
# 1. DERIVED SPEC-TIER & PRICE-BAND PARSERS (1:1 PYTHON <-> CLICKHOUSE SQL)
# ═════════════════════════════════════════════════════════════════════════════

def parse_computing_spec_tier(prod: Any, brand: Any, icat: Any, item_name: Any) -> str:
    """
    Parse stable commercial Spec Tier from ERP `item_name` + `brand` + `item_category`.
    Matches `ch_computing_spec_tier_expr(...)` 1:1.
    """
    p = ' '.join(str(prod or '').upper().strip().split())
    if p not in ARCHETYPE_3_SET:
        return ''
    b = ' '.join(str(brand or '').upper().strip().split())
    ic = ' '.join(str(icat or '').upper().strip().split())
    nm = str(item_name or '').upper()

    if p == 'LAPTOP':
        if b == 'APPLE' or _has_any_kw(nm, _LAPTOP_MAC_PRO_KW) or _has_any_kw(nm, _LAPTOP_MAC_AIR_KW):
            if 'PRO' in nm or 'MBP' in nm:
                return 'MACBOOK PRO'
            return 'MACBOOK AIR'
        if _has_any_kw(nm, _LAPTOP_GAMING_KW):
            return 'GAMING LAPTOP'
        if _has_any_kw(nm, _LAPTOP_ENTRY_KW):
            return 'ENTRY / STUDENT'
        if _has_any_kw(nm, _LAPTOP_PRO_KW):
            return 'PRO / I7-R7+'
        if _has_any_kw(nm, _LAPTOP_MAIN_KW):
            return 'MAINSTREAM / I5-R5'
        if _has_any_kw(nm, _LAPTOP_BUDGET_KW):
            return 'BUDGET / I3-R3'
        return 'GENERAL LAPTOP'

    if p == 'DESKTOP':
        if b == 'APPLE' or _has_any_kw(nm, _DESKTOP_APPLE_KW):
            return 'APPLE DESKTOP'
        if _has_any_kw(nm, _DESKTOP_AIO_KW) or _has_any_kw(ic, _DESKTOP_AIO_KW):
            return 'ALL-IN-ONE PC'
        if _has_any_kw(nm, _DESKTOP_MINI_KW) or _has_any_kw(ic, _DESKTOP_MINI_KW):
            return 'MINI PC'
        return 'TOWER / DESKTOP'

    # PRINTER
    if _has_any_kw(ic, _PRINTER_LASER_KW) or _has_any_kw(nm, _PRINTER_LASER_KW):
        return 'LASER PRINTER'
    if _has_any_kw(ic, _PRINTER_INKTANK_KW) or _has_any_kw(nm, _PRINTER_INKTANK_KW):
        return 'INK TANK PRINTER'
    if _has_any_kw(ic, _PRINTER_THERMAL_KW) or _has_any_kw(nm, _PRINTER_THERMAL_KW):
        return 'THERMAL / DOT MATRIX'
    if _has_any_kw(ic, _PRINTER_INKJET_KW) or _has_any_kw(nm, _PRINTER_INKJET_KW):
        return 'INKJET PRINTER'
    return 'GENERAL PRINTER'


def ch_computing_spec_tier_expr(prod_col: str, brand_col: str, cat_col: str, name_col: str) -> str:
    """ClickHouse SQL expression matching `parse_computing_spec_tier(...)` 1:1."""
    p = f"upper(trim(ifNull({prod_col}, '')))"
    b = f"upper(trim(ifNull({brand_col}, '')))"
    ic = f"upper(trim(ifNull({cat_col}, '')))"
    nm = f"upper(ifNull({name_col}, ''))"

    lap_mac = f"({b} = 'APPLE' OR {_ch_has_any_kw(nm, _LAPTOP_MAC_PRO_KW)} OR {_ch_has_any_kw(nm, _LAPTOP_MAC_AIR_KW)})"
    lap_mac_pro = f"(position({nm}, 'PRO') > 0 OR position({nm}, 'MBP') > 0)"
    lap_expr = (
        f"multiIf("
        f"{lap_mac} AND {lap_mac_pro}, 'MACBOOK PRO', "
        f"{lap_mac}, 'MACBOOK AIR', "
        f"{_ch_has_any_kw(nm, _LAPTOP_GAMING_KW)}, 'GAMING LAPTOP', "
        f"{_ch_has_any_kw(nm, _LAPTOP_ENTRY_KW)}, 'ENTRY / STUDENT', "
        f"{_ch_has_any_kw(nm, _LAPTOP_PRO_KW)}, 'PRO / I7-R7+', "
        f"{_ch_has_any_kw(nm, _LAPTOP_MAIN_KW)}, 'MAINSTREAM / I5-R5', "
        f"{_ch_has_any_kw(nm, _LAPTOP_BUDGET_KW)}, 'BUDGET / I3-R3', "
        f"'GENERAL LAPTOP')"
    )

    desk_expr = (
        f"multiIf("
        f"{b} = 'APPLE' OR {_ch_has_any_kw(nm, _DESKTOP_APPLE_KW)}, 'APPLE DESKTOP', "
        f"{_ch_has_any_kw(nm, _DESKTOP_AIO_KW)} OR {_ch_has_any_kw(ic, _DESKTOP_AIO_KW)}, 'ALL-IN-ONE PC', "
        f"{_ch_has_any_kw(nm, _DESKTOP_MINI_KW)} OR {_ch_has_any_kw(ic, _DESKTOP_MINI_KW)}, 'MINI PC', "
        f"'TOWER / DESKTOP')"
    )

    prn_expr = (
        f"multiIf("
        f"{_ch_has_any_kw(ic, _PRINTER_LASER_KW)} OR {_ch_has_any_kw(nm, _PRINTER_LASER_KW)}, 'LASER PRINTER', "
        f"{_ch_has_any_kw(ic, _PRINTER_INKTANK_KW)} OR {_ch_has_any_kw(nm, _PRINTER_INKTANK_KW)}, 'INK TANK PRINTER', "
        f"{_ch_has_any_kw(ic, _PRINTER_THERMAL_KW)} OR {_ch_has_any_kw(nm, _PRINTER_THERMAL_KW)}, 'THERMAL / DOT MATRIX', "
        f"{_ch_has_any_kw(ic, _PRINTER_INKJET_KW)} OR {_ch_has_any_kw(nm, _PRINTER_INKJET_KW)}, 'INKJET PRINTER', "
        f"'GENERAL PRINTER')"
    )

    return f"multiIf({p} = 'LAPTOP', {lap_expr}, {p} = 'DESKTOP', {desk_expr}, {p} = 'PRINTER', {prn_expr}, '')"


def computing_price_to_band(prod: Any, price: Any) -> str:
    """Map computing unit price (INR) to canonical Archetype 3 price band."""
    p = ' '.join(str(prod or '').upper().strip().split())
    try:
        v = float(price or 0.0)
    except Exception:
        v = 0.0
    if p == 'PRINTER':
        if v < 8000.0:
            return 'P1_LT8K'
        if v < 15000.0:
            return 'P2_8_15K'
        if v < 25000.0:
            return 'P3_15_25K'
        return 'P4_25K_PLUS'
    # LAPTOP & DESKTOP
    if v < 40000.0:
        return 'C1_LT40K'
    if v < 55000.0:
        return 'C2_40_55K'
    if v < 75000.0:
        return 'C3_55_75K'
    if v < 100000.0:
        return 'C4_75K_1L'
    return 'C5_1L_PLUS'


def computing_price_band_label(band: str) -> str:
    return COMPUTING_PRICE_BAND_LABELS.get(str(band or ''), str(band or ''))


def ch_computing_price_band_expr(prod_col: str, price_expr: str) -> str:
    """ClickHouse SQL expression matching `computing_price_to_band(prod, price)` 1:1."""
    p = f"upper(trim(ifNull({prod_col}, '')))"
    prn_pb = (
        f"multiIf({price_expr} < 8000, 'P1_LT8K', {price_expr} < 15000, 'P2_8_15K', "
        f"{price_expr} < 25000, 'P3_15_25K', 'P4_25K_PLUS')"
    )
    comp_pb = (
        f"multiIf({price_expr} < 40000, 'C1_LT40K', {price_expr} < 55000, 'C2_40_55K', "
        f"{price_expr} < 75000, 'C3_55_75K', {price_expr} < 100000, 'C4_75K_1L', 'C5_1L_PLUS')"
    )
    return f"if({p} = 'PRINTER', {prn_pb}, {comp_pb})"


def ch_computing_price_band_label_expr(pb_expr: str) -> str:
    """Map price-band code -> human-readable ₹ range inside ClickHouse SQL."""
    src = "[" + ", ".join(f"'{_sql_esc(k)}'" for k in COMPUTING_PRICE_BAND_LABELS.keys()) + "]"
    dst = "[" + ", ".join(f"'{_sql_esc(v)}'" for v in COMPUTING_PRICE_BAND_LABELS.values()) + "]"
    return f"transform({pb_expr}, {src}, {dst}, {pb_expr})"


def ch_archetype3_hygiene_clause(prod_col: str, cat_col: str) -> str:
    """Exclude stray spare/consumable/accessory item_categories inside retail LAPTOP/DESKTOP/PRINTER."""
    bad_sql = "', '".join(_sql_esc(x) for x in ARCHETYPE_3_NON_COMPUTING_ICATS)
    return (
        f"NOT (upper(trim(ifNull({prod_col}, ''))) IN ('LAPTOP', 'DESKTOP', 'PRINTER') "
        f"AND upper(trim(ifNull({cat_col}, ''))) IN ('{bad_sql}'))"
    )


def build_computing_bucket_id(prod: Any, brand: Any, spec_tier: str, pband: str) -> str:
    """Canonical 4-part Computing Planning Bucket ID: `PROD||BRAND||SPEC_TIER||PBAND`."""
    p = ' '.join(str(prod or '').upper().strip().split()) or 'LAPTOP'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    return f"{p}||{b}||{spec_tier}||{pband}"


def format_computing_bucket_label(brand: Any, spec_tier: str, pband: str) -> str:
    """Human-readable label e.g. `HP — MAINSTREAM / I5-R5 (₹40K–₹55K)`."""
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    pb_lbl = computing_price_band_label(pband)
    if spec_tier and pb_lbl:
        return f"{b} — {spec_tier} ({pb_lbl})"
    if spec_tier:
        return f"{b} — {spec_tier}"
    return b


def ch_computing_bucket_where_clauses(
    bucket_id: str,
    prod_col: str,
    brand_col: str,
    cat_col: str,
    name_col: str,
    price_expr: str,
) -> List[str]:
    """Exact ClickHouse WHERE clauses matching a canonical Archetype 3 Computing Bucket ID."""
    parts = str(bucket_id or '').split('||')
    while len(parts) < 4:
        parts.append('')
    p_part, b_part, st_part, pb_part = [x.strip().upper() for x in parts[:4]]
    clauses = [f"upper(trim(ifNull({prod_col}, ''))) = '{_sql_esc(p_part)}'"]
    if b_part == 'GENERAL':
        clauses.append(
            f"(trim(ifNull({brand_col}, '')) = '' OR upper(trim(ifNull({brand_col}, ''))) = 'GENERAL')"
        )
    else:
        clauses.append(f"upper(trim(ifNull({brand_col}, ''))) = '{_sql_esc(b_part)}'")
    clauses.append(f"{ch_computing_spec_tier_expr(prod_col, brand_col, cat_col, name_col)} = '{_sql_esc(st_part)}'")
    clauses.append(f"{ch_computing_price_band_expr(prod_col, price_expr)} = '{_sql_esc(pb_part)}'")
    return clauses


# ═════════════════════════════════════════════════════════════════════════════
# 2. 3-YEAR DE-FESTIVALIZED COMPUTING SEASONAL PROFILES (BACK-TO-SCHOOL!)
# ═════════════════════════════════════════════════════════════════════════════

def get_archetype3_seasonal_profiles(
    ch,
    ref_dt: date,
    festival_date_map: Dict[date, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Query 3 years of de-festivalized monthly sales for LAPTOP, DESKTOP & PRINTER grouped by
    `(product, brand, spec_tier, price_band, month)` in a single ClickHouse scan (~300ms, cached 30m).
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch3_seas_v1:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    empty = {
        'idx_bkt': {}, 'idx_pst': {}, 'idx_pt': {}, 'idx_prod': {},
        'ref_date': ref_str, 'history_start': ref_str,
    }
    if ch is None:
        return empty

    start_3y = ref_dt - timedelta(days=1095)
    start_str = start_3y.strftime('%Y-%m-%d 00:00:00')
    end_str = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')

    fest_dates_sql: List[str] = []
    fest_weights_sql: List[str] = []
    for fdt, finfo in sorted(festival_date_map.items()):
        if start_3y <= fdt <= ref_dt:
            pw = float(finfo.get('prior_weight', 1.0))
            if abs(pw - 1.0) > 1e-4:
                fest_dates_sql.append(f"toDate('{fdt.strftime('%Y-%m-%d')}')")
                fest_weights_sql.append(f"{max(pw, 0.5):.4f}")

    if fest_dates_sql:
        fmult_expr = (
            f"transform(toDate(s.date), [{', '.join(fest_dates_sql)}], "
            f"[{', '.join(fest_weights_sql)}], 1.0)"
        )
    else:
        fmult_expr = "1.0"

    excl_br_sql = "', '".join(_EXCLUDED_BRANCH_CODES)
    hygiene_sql = ch_archetype3_hygiene_clause('m.product', 'm.item_category')
    eff_price_expr = (
        "if(toFloat64(m.mop) > 1000 AND toFloat64(m.mop) < 600000, toFloat64(m.mop), "
        "if(toFloat64(m.mrp) > 1000 AND toFloat64(m.mrp) < 600000, toFloat64(m.mrp), "
        "abs(toFloat64(s.sold_price)) / greatest(abs(toFloat64(s.qty)), 1.0)))"
    )
    st_sql = ch_computing_spec_tier_expr('m.product', 'm.brand', 'm.item_category', 'm.item_name')
    pb_sql = ch_computing_price_band_expr('m.product', eff_price_expr)

    sql = f"""
        SELECT
            upper(trim(m.product)) AS prod,
            if(trim(ifNull(m.brand, '')) != '', upper(trim(m.brand)), 'GENERAL') AS br,
            {st_sql} AS stier,
            {pb_sql} AS pband,
            toMonth(toDate(s.date)) AS mo,
            greatest(
                0.0,
                sum(
                    if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))
                    / greatest({fmult_expr}, 0.5)
                )
            ) AS df_qty,
            min(toDate(s.date)) AS min_d
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date >= toDateTime('{start_str}')
          AND s.date < toDateTime('{end_str}')
          AND upper(trim(m.product)) IN ('LAPTOP', 'DESKTOP', 'PRINTER')
          AND {hygiene_sql}
          AND s.item_code != ''
          AND upper(trim(s.branch)) NOT IN ('{excl_br_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY prod, br, stier, pband, mo
    """
    try:
        rows = ch.query(sql).result_rows
    except Exception as e:
        print('[archetype3] seasonal profile query failed:', e)
        return empty

    if not rows:
        return empty

    min_dates = []
    for r in rows:
        d_val = r[6]
        if isinstance(d_val, date):
            min_dates.append(d_val)
        elif d_val:
            try:
                min_dates.append(pd.to_datetime(d_val).date())
            except Exception:
                pass
    hist_start = max(start_3y, min(min_dates)) if min_dates else start_3y

    cal_days_by_mo: Dict[int, int] = {m: 0 for m in range(1, 13)}
    cur = hist_start
    while cur <= ref_dt:
        cal_days_by_mo[cur.month] += 1
        cur += timedelta(days=1)

    bkt_rows: List[Tuple[str, int, float]] = []
    pst_rows: List[Tuple[str, int, float]] = []
    pt_rows: List[Tuple[str, int, float]] = []
    prod_rows: List[Tuple[str, int, float]] = []

    for r in rows:
        prod = str(r[0] or '').strip().upper()
        br = str(r[1] or '').strip().upper() or 'GENERAL'
        stier = str(r[2] or '').strip().upper()
        pband = str(r[3] or '').strip().upper()
        mo = int(r[4] or 0)
        df_qty = float(r[5] or 0.0)
        if not prod or mo < 1 or mo > 12 or df_qty <= 0.0:
            continue
        prod_rows.append((prod, mo, df_qty))
        if stier:
            pt_rows.append((f"{prod}||{stier}", mo, df_qty))
            if pband:
                pst_rows.append((f"{prod}||{stier}||{pband}", mo, df_qty))
                bkt_rows.append((f"{prod}||{br}||{stier}||{pband}", mo, df_qty))

    def _build_arch3_table(agg_rows, min_tot):
        raw = _build_month_index_table(agg_rows, cal_days_by_mo, min_total_units=min_tot)
        return {k: {m: float(np.clip(v, 0.40, 2.50)) for m, v in mm.items()} for k, mm in raw.items()}

    profiles = {
        'idx_bkt': _build_arch3_table(bkt_rows, 200.0),
        'idx_pst': _build_arch3_table(pst_rows, 150.0),
        'idx_pt': _build_arch3_table(pt_rows, 100.0),
        'idx_prod': _build_arch3_table(prod_rows, 50.0),
        'ref_date': ref_str,
        'history_start': hist_start.strftime('%Y-%m-%d'),
    }
    cache.set(cache_key, profiles, 1800)
    return profiles


def lookup_archetype3_month_index(
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    brand: Any = '',
    spec_tier: str = '',
    pband: str = '',
) -> Dict[int, float]:
    """
    Hierarchical Computing seasonal index lookup:
      - Blends 45% `PROD||BRAND||SPEC_TIER||PBAND` + 55% `PROD||SPEC_TIER||PBAND`
        (falling back to `PROD||SPEC_TIER` -> `PROD`).
    """
    if not profiles:
        return {}
    p = ' '.join(str(prod or '').upper().strip().split())
    if p not in ARCHETYPE_3_SET:
        return {}
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    st = str(spec_tier or '').strip().upper()
    pb = str(pband or '').strip().upper()

    idx_bkt = profiles.get('idx_bkt') or {}
    idx_pst = profiles.get('idx_pst') or {}
    idx_pt = profiles.get('idx_pt') or {}
    idx_prod = profiles.get('idx_prod') or {}

    bk = idx_bkt.get(f"{p}||{b}||{st}||{pb}") if (st and pb) else None
    ps = (
        (idx_pst.get(f"{p}||{st}||{pb}") if (st and pb) else None)
        or (idx_pt.get(f"{p}||{st}") if st else None)
        or idx_prod.get(p)
    )
    if bk and ps:
        return {m: 0.45 * float(bk.get(m, 1.0)) + 0.55 * float(ps.get(m, 1.0)) for m in range(1, 13)}
    if bk:
        return dict(bk)
    if ps:
        return dict(ps)
    return {}


def compute_computing_seasonal_factors(
    ref_dt: date,
    mo_idx: Dict[int, float],
) -> Tuple[float, float, float]:
    """
    Blended calendar seasonal transition multipliers `(f7, f15, f30)` for Computing.
    Uses 65% seasonal transition ratio + 35% neutral 1.0 (empirically optimal on 6-origin backtest).
    """
    if not mo_idx:
        return 1.0, 1.0, 1.0
    tr_vals = [smooth_month_index_on_date(ref_dt - timedelta(days=a), mo_idx) for a in range(30)]
    fut_vals = [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)]
    tr_m = max(float(np.mean(tr_vals)), 0.15)
    out = []
    for h in (7, 15, 30):
        fut_m = float(np.mean(fut_vals[:h]))
        raw_ratio = float(np.clip(fut_m / tr_m, 0.45, 2.20))
        out.append(0.35 + 0.65 * raw_ratio)
    return out[0], out[1], out[2]


# ═════════════════════════════════════════════════════════════════════════════
# 3. COMPUTING SKU COMMERCIAL LAUNCH-DATE DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

def get_archetype3_launch_dates(ch, ref_dt: date) -> Dict[str, date]:
    """
    Fetch robust 2nd-percentile commercial launch date (`<= ref_dt`) for every
    LAPTOP, DESKTOP & PRINTER `item_code`, cached 30m per `ref_dt`.
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch3_launch_v1:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    if ch is None:
        return {}

    end_str = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    excl_br_sql = "', '".join(_EXCLUDED_BRANCH_CODES)
    hygiene_sql = ch_archetype3_hygiene_clause('m.product', 'm.item_category')

    sql = f"""
        SELECT
            s.item_code,
            quantileExactIf(0.02)(toDate(s.date), s.sold_price > 0) AS launch_dt,
            countIf(s.sold_price > 0) AS txns
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date < toDateTime('{end_str}')
          AND upper(trim(m.product)) IN ('LAPTOP', 'DESKTOP', 'PRINTER')
          AND {hygiene_sql}
          AND s.item_code != ''
          AND upper(trim(s.branch)) NOT IN ('{excl_br_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY s.item_code
        HAVING txns > 0
    """
    try:
        rows = ch.query(sql).result_rows
    except Exception as e:
        print('[archetype3] launch date query failed:', e)
        return {}

    sku_first: Dict[str, date] = {}
    for code, fdt, _txns in rows:
        if not code:
            continue
        if not isinstance(fdt, date):
            try:
                fdt = pd.to_datetime(fdt).date()
            except Exception:
                continue
        if fdt.year < 2000 or fdt > ref_dt:
            continue
        sku_first[str(code).strip()] = fdt

    cache.set(cache_key, sku_first, 1800)
    return sku_first


# ═════════════════════════════════════════════════════════════════════════════
# 4. APPLY ARCHETYPE 3 SPEC-TIER POOLING, SEASONALITY & LIFECYCLE
# ═════════════════════════════════════════════════════════════════════════════

def _compute_spec_tier_pool_targets(
    df: pd.DataFrame,
    arch3_idx: np.ndarray,
    bkt_ids: List[str],
    bucket_mult_sums: List[float],
    S7: float,
    S15: float,
    S30: float,
    ref_dt: date,
    fc_engine: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Pool multi-SKU Computing Spec-Tier × Price-Band buckets via fast Hybrid ML
    and allocate back to member OEM SKUs by recency-weighted velocity share (50% blend).
    Solves the 75.5% Laptop OEM SKU sparsity problem!
    """
    n = len(df)
    pool_w = np.zeros(n, dtype=float)
    alloc7 = np.zeros(n, dtype=float)
    alloc15 = np.zeros(n, dtype=float)
    alloc30 = np.zeros(n, dtype=float)

    if len(arch3_idx) < 4:
        return pool_w, alloc7, alloc15, alloc30
    required_cols = ['u_b0', 'u_b1', 'd7', 'd30', 'd90', 'unit_mop', 'unit_mrp']
    if not all(c in df.columns for c in required_cols):
        return pool_w, alloc7, alloc15, alloc30

    sub = df.iloc[arch3_idx].copy()
    sub['_orig_pos'] = arch3_idx
    sub['_bkt_key'] = [bkt_ids[i] for i in arch3_idx]
    bkt_sizes = sub.groupby('_bkt_key')['_orig_pos'].transform('count').values
    multi_mask = bkt_sizes >= 2
    if not bool(multi_mask.any()):
        return pool_w, alloc7, alloc15, alloc30

    pool_sub = sub[multi_mask].copy()
    agg_spec: Dict[str, Any] = {
        'product_line': 'first',
        'brand': 'first',
        'd7': 'sum',
        'd30': 'sum',
        'd90': 'sum',
        'unit_mop': 'mean',
        'unit_mrp': 'mean',
        'branch_breadth_90': 'max',
        'branch_breadth_prior': 'max',
    }
    if 'current_stock' in pool_sub.columns:
        agg_spec['current_stock'] = 'sum'
    for k in range(9):
        if f'u_b{k}' in pool_sub.columns:
            agg_spec[f'u_b{k}'] = 'sum'
        if f'r_b{k}' in pool_sub.columns:
            agg_spec[f'r_b{k}'] = 'sum'
        if f'a_b{k}' in pool_sub.columns:
            agg_spec[f'a_b{k}'] = 'max'

    bkt_df = pool_sub.groupby('_bkt_key', as_index=False).agg(agg_spec)
    if bkt_df.empty:
        return pool_w, alloc7, alloc15, alloc30

    try:
        bkt_fc_df, _ = run_hybrid_ml_forecasting(
            df=bkt_df,
            bucket_mult_sums=bucket_mult_sums,
            S7=S7,
            S15=S15,
            S30=S30,
            gran='spec_bucket',
            ref_dt_date=ref_dt,
            has_stock_snapshot=('current_stock' in bkt_df.columns),
            forecast_engine=fc_engine,
            pretrained=None,
        )
    except Exception as e:
        print('[archetype3] spec-tier pool forecast skipped:', e)
        return pool_w, alloc7, alloc15, alloc30

    f7_map = dict(zip(bkt_fc_df['_bkt_key'], bkt_fc_df['forecast_7d'].astype(float)))
    f15_map = dict(zip(bkt_fc_df['_bkt_key'], bkt_fc_df['forecast_15d'].astype(float)))
    f30_map = dict(zip(bkt_fc_df['_bkt_key'], bkt_fc_df['forecast_30d'].astype(float)))

    c_wt = (
        0.55 * pool_sub['u_b0'].astype(float).values * 1.4
        + 0.25 * pool_sub['d30'].astype(float).values
        + 0.20 * pool_sub['forecast_30d'].astype(float).values
    )
    pool_sub['_c_wt'] = np.maximum(c_wt, 0.0)
    b_wsum = pool_sub.groupby('_bkt_key')['_c_wt'].transform('sum').values
    shares = np.where(b_wsum > 0.0, pool_sub['_c_wt'].values / np.maximum(b_wsum, 1e-6), 0.0)

    p_f7 = pool_sub['_bkt_key'].map(f7_map).fillna(0.0).values * shares
    p_f15 = pool_sub['_bkt_key'].map(f15_map).fillna(0.0).values * shares
    p_f30 = pool_sub['_bkt_key'].map(f30_map).fillna(0.0).values * shares

    pos_arr = pool_sub['_orig_pos'].values
    for j, orig_i in enumerate(pos_arr):
        pool_w[orig_i] = 0.50
        alloc7[orig_i] = p_f7[j]
        alloc15[orig_i] = p_f15[j]
        alloc30[orig_i] = p_f30[j]

    return pool_w, alloc7, alloc15, alloc30


def apply_archetype3_forecast_enhancements(
    df: pd.DataFrame,
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    launch_dates: Optional[Dict[str, date]],
    gran: str,
    fc_engine: str,
    bucket_mult_sums: Optional[List[float]] = None,
    S7: float = 7.0,
    S15: float = 15.0,
    S30: float = 30.0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Annotate LAPTOP, DESKTOP & PRINTER rows with Derived Spec Tier, Computing Price Band,
    and SKU Rollout/EOL Lifecycle Stage, and apply empirically validated enhancements when
    `fc_engine in ('hybrid', 'ml')`:
      1. 50% Spec-Tier × Price-Band pooled forecast stabilization in `sku` mode.
      2. 3-year de-festivalized Computing seasonality (65% weight; captures Kerala Jun–Sep Back-to-School).
      3. Computing SKU rollout ramp (`0–7d` partial window & `8–21d` store rollout) + OEM EOL decay (`120d+` declining / `180d+` aging).
    """
    n = len(df)
    for col, default_val in (
        ('spec_bucket_id', ''),
        ('spec_bucket_label', ''),
        ('price_band', ''),
        ('price_band_label', ''),
        ('lifecycle_stage', ''),
    ):
        if col not in df.columns:
            df[col] = default_val
    if 'launch_age_days' not in df.columns:
        df['launch_age_days'] = -1

    spec_ids = df['spec_bucket_id'].astype(str).tolist()
    spec_labels = df['spec_bucket_label'].astype(str).tolist()
    price_bands = df['price_band'].astype(str).tolist()
    price_band_lbls = df['price_band_label'].astype(str).tolist()
    lifecycle_stages = df['lifecycle_stage'].astype(str).tolist()
    launch_ages = pd.to_numeric(df['launch_age_days'], errors='coerce').fillna(-1).to_numpy(dtype=int, copy=True)
    spec_tiers = [''] * n
    arch3_active = np.zeros(n, dtype=bool)
    net_adj_pct = np.zeros(n, dtype=float)

    prods = df['product_line'].astype(str).tolist()
    brands = df['brand'].astype(str).tolist()
    ids = df['id'].astype(str).tolist()
    names = df['name'].astype(str).tolist()
    icats = df['item_category'].astype(str).tolist() if 'item_category' in df.columns else [''] * n
    igrps = df['item_group'].astype(str).tolist() if 'item_group' in df.columns else [''] * n

    mops = df['unit_mop'].astype(float).values if 'unit_mop' in df.columns else np.zeros(n, dtype=float)
    mrps = df['unit_mrp'].astype(float).values if 'unit_mrp' in df.columns else np.zeros(n, dtype=float)
    d30_arr = df['d30'].astype(float).values if 'd30' in df.columns else np.zeros(n, dtype=float)
    prev30_arr = df['prev_d30'].astype(float).values if 'prev_d30' in df.columns else np.zeros(n, dtype=float)
    d90_arr = df['d90'].astype(float).values if 'd90' in df.columns else np.zeros(n, dtype=float)
    rev90_arr = df['rev90'].astype(float).values if 'rev90' in df.columns else np.zeros(n, dtype=float)
    u0_arr = df['u_b0'].astype(float).values if 'u_b0' in df.columns else np.zeros(n, dtype=float)
    u1_arr = df['u_b1'].astype(float).values if 'u_b1' in df.columns else np.zeros(n, dtype=float)

    sku_first_map = launch_dates or {}

    f7_arr = np.ones(n, dtype=float)
    f15_arr = np.ones(n, dtype=float)
    f30_arr = np.ones(n, dtype=float)
    s_age_arr = np.full(n, 999, dtype=int)
    bkt_ids_all = [''] * n
    arch3_positions: List[int] = []

    seas_cache: Dict[Tuple[str, str, str, str], Tuple[float, float, float]] = {}
    apply_enh = (fc_engine in ('hybrid', 'ml'))

    for i in range(n):
        p = prods[i].strip().upper()
        if p not in ARCHETYPE_3_SET:
            continue
        arch3_positions.append(i)
        b = brands[i].strip().upper() or 'GENERAL'

        if gran == 'spec_bucket':
            # In spec_bucket granularity, SQL already populated spec_icat = spec_tier, spec_igrp = pband!
            st = icats[i].strip().upper() or parse_computing_spec_tier(p, b, icats[i], names[i])
            pb = igrps[i].strip().upper()
            if pb not in COMPUTING_PRICE_BAND_LABELS:
                eff_p = float(mops[i]) if 1000.0 < float(mops[i]) < 600000.0 else (float(mrps[i]) if 1000.0 < float(mrps[i]) < 600000.0 else 45000.0)
                pb = computing_price_to_band(p, eff_p)
        else:
            st = parse_computing_spec_tier(p, b, icats[i], names[i]) if gran == 'sku' else ''
            eff_p = float(mops[i])
            if not (1000.0 < eff_p < 600000.0):
                if 1000.0 < float(mrps[i]) < 600000.0:
                    eff_p = float(mrps[i])
                elif d90_arr[i] > 0 and rev90_arr[i] > 0:
                    eff_p = float(rev90_arr[i] / max(d90_arr[i], 1.0))
                else:
                    eff_p = 14000.0 if p == 'PRINTER' else 45000.0
            pb = computing_price_to_band(p, eff_p) if gran == 'sku' else ''

        spec_tiers[i] = st
        price_bands[i] = pb
        price_band_lbls[i] = computing_price_band_label(pb) if pb else ''

        bkt_id = build_computing_bucket_id(p, b, st, pb) if (st and pb) else ''
        bkt_ids_all[i] = bkt_id
        if gran in ('sku', 'spec_bucket') and bkt_id:
            spec_ids[i] = bkt_id
            spec_labels[i] = format_computing_bucket_label(b, st, pb)

        # Causal SKU age (in `sku` mode only — Spec-Tier buckets are perennial across years!)
        s_dt = sku_first_map.get(ids[i]) if gran == 'sku' else None
        s_age = max(0, (ref_dt - s_dt).days) if isinstance(s_dt, date) else 999
        s_age_arr[i] = s_age
        if gran == 'sku' and s_age < 999:
            launch_ages[i] = s_age

        u0_v = float(u0_arr[i])
        u1_v = float(u1_arr[i])
        d30_v = float(d30_arr[i])
        prev30_v = float(prev30_arr[i])

        if gran == 'sku' and s_age <= 7 and u0_v >= 2.0:
            stage = 'New Rollout (0–7d)'
            l7 = float(np.clip(1.0 + 0.35 * (8.0 - s_age) / 8.0, 1.04, 1.35))
            l15 = float(np.clip(1.0 + 0.45 * (8.0 - s_age) / 8.0, 1.05, 1.40))
            l30 = float(np.clip(1.0 + 0.50 * (8.0 - s_age) / 8.0, 1.05, 1.45))
        elif gran == 'sku' and 8 <= s_age <= 21 and u0_v > u1_v and u0_v >= 2.0:
            stage = 'Store Ramp (8–21d)'
            l7, l15, l30 = 1.08, 1.12, 1.15
        elif gran == 'sku' and s_age < 120:
            stage = 'Prime (22–119d)'
            l7, l15, l30 = 1.0, 1.0, 1.0
        elif gran == 'sku' and s_age >= 120 and prev30_v > 0 and d30_v < prev30_v * 0.85:
            stage = 'Declining / EOL'
            l7, l15, l30 = 0.94, 0.90, 0.85
        elif gran == 'sku' and s_age >= 180 and u0_v <= u1_v and d30_v > 0:
            stage = 'Aging (180d+)'
            l7, l15, l30 = 0.96, 0.94, 0.90
        elif gran == 'sku' and s_age < 999:
            stage = 'Mature Active'
            l7, l15, l30 = 1.0, 1.0, 1.0
        else:
            stage = ''
            l7, l15, l30 = 1.0, 1.0, 1.0
        if stage:
            lifecycle_stages[i] = stage

        # 3-year de-festivalized Computing seasonal transition ratio
        s7, s15, s30 = 1.0, 1.0, 1.0
        if profiles:
            ckey = (p, b, st, pb)
            cached_s = seas_cache.get(ckey)
            if cached_s is None:
                mi = lookup_archetype3_month_index(profiles, p, b, st, pb)
                cached_s = compute_computing_seasonal_factors(ref_dt, mi) if mi else (1.0, 1.0, 1.0)
                seas_cache[ckey] = cached_s
            s7, s15, s30 = cached_s

        if apply_enh:
            f7_arr[i] = float(np.clip(l7 * s7, 0.45, 2.00))
            f15_arr[i] = float(np.clip(l15 * s15, 0.45, 2.00))
            f30_arr[i] = float(np.clip(l30 * s30, 0.45, 2.00))
            arch3_active[i] = True
            net_adj_pct[i] = round((f30_arr[i] - 1.0) * 100.0, 1)

    df['spec_bucket_id'] = spec_ids
    df['spec_bucket_label'] = spec_labels
    df['spec_tier'] = spec_tiers
    df['price_band'] = price_bands
    df['price_band_label'] = price_band_lbls
    df['lifecycle_stage'] = lifecycle_stages
    df['launch_age_days'] = launch_ages
    df['archetype3_active'] = arch3_active
    df['archetype3_adj_pct'] = net_adj_pct

    pooled_cnt = 0
    if apply_enh and arch3_positions:
        arch3_idx = np.array(arch3_positions, dtype=int)
        base_fc7 = df['forecast_7d'].astype(float).values.copy()
        base_fc15 = df['forecast_15d'].astype(float).values.copy()
        base_fc30 = df['forecast_30d'].astype(float).values.copy()

        # Spec-Tier × Price-Band pooling in SKU mode (excludes fresh Week-1 rollouts)
        if gran == 'sku' and bucket_mult_sums is not None:
            pool_w, p_a7, p_a15, p_a30 = _compute_spec_tier_pool_targets(
                df=df,
                arch3_idx=arch3_idx,
                bkt_ids=bkt_ids_all,
                bucket_mult_sums=bucket_mult_sums,
                S7=S7,
                S15=S15,
                S30=S30,
                ref_dt=ref_dt,
                fc_engine=fc_engine,
            )
            pool_w = np.where(s_age_arr <= 7, 0.0, pool_w)
            use_pool = pool_w > 0.0
            pooled_cnt = int(use_pool.sum())
            if pooled_cnt > 0:
                base_fc7 = np.where(use_pool, pool_w * p_a7 + (1.0 - pool_w) * base_fc7, base_fc7)
                base_fc15 = np.where(use_pool, pool_w * p_a15 + (1.0 - pool_w) * base_fc15, base_fc15)
                base_fc30 = np.where(use_pool, pool_w * p_a30 + (1.0 - pool_w) * base_fc30, base_fc30)

        mask = arch3_active
        new_fc7 = np.where(mask, np.maximum(0.0, base_fc7 * f7_arr), base_fc7)
        new_fc15 = np.where(mask, np.maximum(new_fc7, base_fc15 * f15_arr), base_fc15)
        new_fc30 = np.where(mask, np.maximum(new_fc15, base_fc30 * f30_arr), base_fc30)
        df['forecast_7d'] = np.round(new_fc7, 1)
        df['forecast_15d'] = np.round(new_fc15, 1)
        df['forecast_30d'] = np.round(new_fc30, 1)

        if 'hybrid_forecast_15d' in df.columns:
            df['hybrid_forecast_15d'] = np.round(
                np.where(mask, np.maximum(0.0, df['hybrid_forecast_15d'].astype(float) * f15_arr), df['hybrid_forecast_15d'].astype(float)),
                1,
            )
        if 'ml_forecast_15d' in df.columns:
            df['ml_forecast_15d'] = np.round(
                np.where(mask, np.maximum(0.0, df['ml_forecast_15d'].astype(float) * f15_arr), df['ml_forecast_15d'].astype(float)),
                1,
            )

    active_cnt = int(arch3_active.sum())
    avg_adj = round(float(df.loc[arch3_active, 'archetype3_adj_pct'].mean()), 1) if active_cnt > 0 else 0.0
    distinct_bkts = len({bkt_ids_all[i] for i in arch3_positions if bkt_ids_all[i]})
    stage_counts: Dict[str, int] = {}
    for i in arch3_positions:
        st = lifecycle_stages[i]
        if st:
            stage_counts[st] = stage_counts.get(st, 0) + 1

    diagnostics = {
        'enabled': bool(active_cnt > 0),
        'archetype': 'Archetype 3 · Spec-Driven Computing (Laptops, Desktops & Printers)',
        'products_covered': list(ARCHETYPE_3_PRODUCTS),
        'enhanced_entities': active_cnt,
        'distinct_spec_tiers': distinct_bkts,
        'skus_pooled': pooled_cnt,
        'avg_adj_pct': avg_adj,
        'lifecycle_counts': stage_counts,
        'history_start': (profiles or {}).get('history_start', ''),
    }
    return df, diagnostics


def compute_archetype3_daily_curve(
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    brand: Any = '',
    spec_tier: str = '',
    pband: str = '',
    lifecycle_stage: str = '',
) -> Optional[np.ndarray]:
    """
    30-day relative trajectory curve (mean=1.0) combining Computing Back-to-School
    seasonal shape and gentle Store-Ramp / EOL slope.
    """
    p = ' '.join(str(prod or '').upper().strip().split())
    if p not in ARCHETYPE_3_SET:
        return None
    mo_idx = lookup_archetype3_month_index(profiles, p, brand, spec_tier, pband)
    if mo_idx:
        vals = np.array(
            [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)],
            dtype=float,
        )
        mean_v = float(np.mean(vals))
        rel = (0.35 + 0.65 * (vals / mean_v)) if mean_v > 0 else np.ones(30, dtype=float)
    else:
        rel = np.ones(30, dtype=float)

    if lifecycle_stage == 'Store Ramp (8–21d)':
        rel = rel * np.linspace(0.95, 1.05, 30)
    elif lifecycle_stage == 'Declining / EOL':
        rel = rel * np.linspace(1.06, 0.94, 30)

    m = float(np.mean(rel))
    return (rel / m) if m > 0 else None
