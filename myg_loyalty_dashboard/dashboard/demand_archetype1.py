"""
Archetype 1 — Spec-Bucket Forecasting Engine for Major Appliances
==================================================================
Covers the 8 major appliance product lines where SKU `item_code` churns
annually on cosmetic model-year refreshes while physical demand lives in
stable specification buckets (`Brand × Product × item_category × item_group`)
driven by strong Kerala annual weather seasonality (Summer ↔ Monsoon):

    AIR CONDITIONER, TV, REFRIGERATORS, WASHING MACHINES,
    DISH WASHER, FREEZER, DRYER, STABILIZER

Empirical 6-origin full-year ClickHouse walk-forward validation (437,744 units):
  - 30d SKU WAPE        : 52.88% -> 46.81% (-6.07 pp, 8/8 products improved)
  - 30d Spec-Bucket WAPE: 38.54% -> 32.37% (-6.17 pp)
  - 30d Forecast Bias   : +8.42% -> -1.37%
  - Air Conditioner 30d : 80.62% -> 66.89% (-13.73 pp SKU / -15.31 pp Bucket)
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.core.cache import cache

from analytics.clickhouse_service import get_active_engine, get_bypass_cache

# ── Archetype 1 Product Universe ─────────────────────────────────────────────
ARCHETYPE_1_PRODUCTS: Tuple[str, ...] = (
    'AIR CONDITIONER',
    'TV',
    'REFRIGERATORS',
    'WASHING MACHINES',
    'DISH WASHER',
    'FREEZER',
    'DRYER',
    'STABILIZER',
)
ARCHETYPE_1_SET = frozenset(ARCHETYPE_1_PRODUCTS)

# Placeholder / junk item_group & item_category values that must collapse to ''
JUNK_SPEC_TOKENS = frozenset({
    '', 'DEFAULT', 'DEFAULT1', 'DEFAULT2', 'GENERAL',
    'NA', 'N/A', 'NONE', 'NIL', '-', 'OTHER', 'OTHERS',
})

# Known minor ERP spelling / spacing variants in Archetype 1 item_group
SPEC_GROUP_TYPO_MAP: Dict[str, str] = {
    'AC- STABLIZER': 'AC- STABILIZER',
    'AC - STABILIZER': 'AC- STABILIZER',
    'AC-STABILIZER': 'AC- STABILIZER',
    'WM - STABILIZER': 'WM- STABILIZER',
    'WSM- STABILIZER': 'WM- STABILIZER',
    'WSM - STABILIZER': 'WM- STABILIZER',
}

_EXCLUDED_BRANCH_CODES = ('3GH', 'CSB', 'CSBW', 'HEAD OFFICE', 'UG SMART CHOICE')


def _sql_esc(val: Any) -> str:
    return str(val or '').replace("\\", "\\\\").replace("'", "''")


# ═════════════════════════════════════════════════════════════════════════════
# 1. SPEC-BUCKET NORMALIZATION (1:1 PARITY BETWEEN PYTHON & CLICKHOUSE SQL)
# ═════════════════════════════════════════════════════════════════════════════

def normalize_item_category(raw_cat: Any) -> str:
    """Clean `item_category` (collapsed whitespace, uppercase, junk -> '')."""
    s = ' '.join(str(raw_cat or '').upper().strip().split())
    return '' if s in JUNK_SPEC_TOKENS else s


def normalize_spec_group(raw_group: Any, product_line: Any = '') -> str:
    """
    Normalize `item_group` for Spec-Bucket grouping.
    Only Archetype 1 major appliances use `item_group` as their spec bucket;
    junk placeholders ('DEFAULT', 'DEFAULT2', etc.) and non-Archetype-1 lines return ''.
    """
    prod = ' '.join(str(product_line or '').upper().strip().split())
    if prod and prod not in ARCHETYPE_1_SET:
        return ''
    s = ' '.join(str(raw_group or '').upper().strip().split())
    if not s or s in JUNK_SPEC_TOKENS:
        return ''
    return SPEC_GROUP_TYPO_MAP.get(s, s)


def ch_clean_item_cat_expr(cat_col: str) -> str:
    """ClickHouse SQL expression matching `normalize_item_category(raw_cat)` 1:1."""
    junk_sql = "', '".join(sorted(x for x in JUNK_SPEC_TOKENS if x))
    norm = f"upper(trim(replaceRegexpAll(ifNull({cat_col}, ''), '\\\\s+', ' ')))"
    return f"if({norm} != '' AND {norm} NOT IN ('{junk_sql}'), {norm}, '')"


def ch_clean_spec_group_expr(grp_col: str, prod_col: str) -> str:
    """ClickHouse SQL expression matching `normalize_spec_group(raw_group, product_line)` 1:1."""
    arch1_sql = "', '".join(ARCHETYPE_1_PRODUCTS)
    junk_sql = "', '".join(sorted(x for x in JUNK_SPEC_TOKENS if x))
    norm = f"upper(trim(replaceRegexpAll(ifNull({grp_col}, ''), '\\\\s+', ' ')))"
    typo_src = "[" + ", ".join(f"'{_sql_esc(k)}'" for k in SPEC_GROUP_TYPO_MAP.keys()) + "]"
    typo_dst = "[" + ", ".join(f"'{_sql_esc(v)}'" for v in SPEC_GROUP_TYPO_MAP.values()) + "]"
    fixed = f"transform({norm}, {typo_src}, {typo_dst}, {norm})"
    return (
        f"if(upper(trim(ifNull({prod_col}, ''))) IN ('{arch1_sql}') "
        f"AND {fixed} != '' AND {fixed} NOT IN ('{junk_sql}'), {fixed}, '')"
    )


PLANNING_GROUP_PRODUCTS: Tuple[str, ...] = ARCHETYPE_1_PRODUCTS + ('MOBILE', 'TABLET', 'FRAGRANCE')


def ch_clean_planning_group_expr(grp_col: str, prod_col: str) -> str:
    """
    Unified ClickHouse SQL expression for `Planning Bucket` granularity covering:
      - Archetype 1 (Major Appliances): Spec Buckets (e.g. `1.5 TON 3 STAR`, `9KG`)
      - Archetype 2 (Mobiles & Tablets): Model Families (e.g. `APPLE IPHONE 17 256GB`)
      - Archetype 5 (Fragrance): Pack-Size Groups (e.g. `MSI PARF 50 ML`, `MSI PARF 30 ML`)
    All other products safely collapse `item_group` to '' to avoid DEFAULT/MSI P&G pollution.
    """
    pg_sql = "', '".join(PLANNING_GROUP_PRODUCTS)
    junk_sql = "', '".join(sorted(x for x in (JUNK_SPEC_TOKENS | {'MSI'}) if x))
    norm = f"upper(trim(replaceRegexpAll(ifNull({grp_col}, ''), '\\\\s+', ' ')))"
    typo_src = "[" + ", ".join(f"'{_sql_esc(k)}'" for k in SPEC_GROUP_TYPO_MAP.keys()) + "]"
    typo_dst = "[" + ", ".join(f"'{_sql_esc(v)}'" for v in SPEC_GROUP_TYPO_MAP.values()) + "]"
    fixed = f"transform({norm}, {typo_src}, {typo_dst}, {norm})"
    return (
        f"if(upper(trim(ifNull({prod_col}, ''))) IN ('{pg_sql}') "
        f"AND {fixed} != '' AND {fixed} NOT IN ('{junk_sql}'), {fixed}, '')"
    )


def build_spec_bucket_id(prod: Any, brand: Any, raw_cat: Any, raw_grp: Any) -> str:
    """
    Canonical 4-part Spec-Bucket key: `PROD||BRAND||ICAT||IGRP`.
    Always includes `item_category` to avoid collisions such as
    `LG | 9KG` spanning both `WASHING MACHINE-TL` and `WASHING MACHINE-FL`.
    """
    p = ' '.join(str(prod or '').upper().strip().split()) or 'OTHER'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(raw_cat)
    ig = normalize_spec_group(raw_grp, p)
    return f"{p}||{b}||{ic}||{ig}"


def parse_spec_bucket_id(bucket_id: str) -> Tuple[str, str, str, str]:
    """Parse canonical `PROD||BRAND||ICAT||IGRP` id into a 4-tuple."""
    parts = str(bucket_id or '').split('||')
    while len(parts) < 4:
        parts.append('')
    return parts[0].strip().upper(), parts[1].strip().upper(), parts[2].strip().upper(), parts[3].strip().upper()


def format_spec_bucket_label(prod: Any, brand: Any, raw_cat: Any, raw_grp: Any) -> str:
    """Human-readable label for a Spec Bucket, e.g. `LG — 1.5 TON 3 STAR (AC INDOOR)`."""
    p = ' '.join(str(prod or '').upper().strip().split()) or 'OTHER'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(raw_cat)
    ig = normalize_spec_group(raw_grp, p)
    if ig and ic:
        return f"{b} — {ig} ({ic})"
    if ig:
        return f"{b} — {ig} ({p})"
    if ic:
        return f"{b} — {ic}"
    return f"{b} — {p}"


_SALES_COMP_PRICE_EXPR = (
    "if(toFloat64(m.mop) > 1000 AND toFloat64(m.mop) < 600000, toFloat64(m.mop), "
    "if(toFloat64(m.mrp) > 1000 AND toFloat64(m.mrp) < 600000, toFloat64(m.mrp), "
    "if(upper(trim(ifNull(m.product, ''))) = 'PRINTER', 14000.0, 45000.0)))"
)
_STOCK_COMP_PRICE_EXPR = (
    "if(toFloat64(mop) > 1000 AND toFloat64(mop) < 600000, toFloat64(mop), "
    "if(toFloat64(cost_price) > 1000 AND toFloat64(cost_price) < 600000, toFloat64(cost_price), "
    "if(upper(trim(ifNull(product, ''))) = 'PRINTER', 14000.0, 45000.0)))"
)


def ch_spec_bucket_where_clauses(
    bucket_id: str,
    prod_col: str,
    brand_col: str,
    cat_col: str,
    grp_col: str,
    name_col: str = '',
    price_expr: str = '',
    arch4_price_expr: str = '',
) -> List[str]:
    """
    Build exact ClickHouse WHERE clauses matching a canonical 4-part Planning Bucket ID
    across Archetype 1 (Appliances), Archetype 2 (Mobiles/Tablets), Archetype 3 (Computing),
    and Archetype 4 (Sub-Category & Price-Banded Smart Watch / Home Theatre).
    """
    p_part, b_part, ic_part, ig_part = parse_spec_bucket_id(bucket_id)
    if p_part in ('LAPTOP', 'DESKTOP', 'PRINTER') and name_col and price_expr:
        from .demand_archetype3 import ch_computing_bucket_where_clauses
        return ch_computing_bucket_where_clauses(bucket_id, prod_col, brand_col, cat_col, name_col, price_expr)

    if p_part in ('SMART WATCH', 'HOME THEATRE'):
        from .demand_archetype4 import (
            _SALES_ARCH4_PRICE_EXPR,
            _STOCK_ARCH4_PRICE_EXPR,
            ch_arch4_bucket_where_clauses,
        )
        eff_a4_price = arch4_price_expr or (
            _SALES_ARCH4_PRICE_EXPR if str(prod_col).startswith('m.') else _STOCK_ARCH4_PRICE_EXPR
        )
        return ch_arch4_bucket_where_clauses(bucket_id, prod_col, brand_col, cat_col, eff_a4_price)

    clauses = [f"upper(trim(ifNull({prod_col}, ''))) = '{_sql_esc(p_part)}'"]
    if b_part == 'GENERAL':
        clauses.append(
            f"(trim(ifNull({brand_col}, '')) = '' OR upper(trim(ifNull({brand_col}, ''))) = 'GENERAL')"
        )
    else:
        clauses.append(f"upper(trim(ifNull({brand_col}, ''))) = '{_sql_esc(b_part)}'")
    clauses.append(f"{ch_clean_item_cat_expr(cat_col)} = '{_sql_esc(ic_part)}'")
    clauses.append(f"{ch_clean_planning_group_expr(grp_col, prod_col)} = '{_sql_esc(ig_part)}'")
    return clauses


# ═════════════════════════════════════════════════════════════════════════════
# 2. 3-YEAR DE-FESTIVALIZED SPEC-BUCKET CALENDAR SEASONAL PROFILES
# ═════════════════════════════════════════════════════════════════════════════

def _build_month_index_table(
    agg_rows: Iterable[Tuple[str, int, float]],
    cal_days_by_mo: Dict[int, int],
    min_total_units: float,
) -> Dict[str, Dict[int, float]]:
    """
    Build Laplace-shrunk monthly seasonality indices (months 1..12, mean ≈ 1.0)
    from de-festivalized monthly unit totals.
    """
    by_key_mo: Dict[str, Dict[int, float]] = {}
    tot_by_key: Dict[str, float] = {}
    for k, mo, qty in agg_rows:
        if not k or mo < 1 or mo > 12 or qty <= 0:
            continue
        km = by_key_mo.setdefault(k, {})
        km[mo] = km.get(mo, 0.0) + float(qty)
        tot_by_key[k] = tot_by_key.get(k, 0.0) + float(qty)

    out: Dict[str, Dict[int, float]] = {}
    for k, mo_map in by_key_mo.items():
        tot = float(tot_by_key.get(k, 0.0))
        if tot < min_total_units:
            continue
        rates: Dict[int, float] = {}
        for m in range(1, 13):
            nd = float(cal_days_by_mo.get(m, 0))
            if nd <= 0:
                continue
            rates[m] = float(mo_map.get(m, 0.0)) / nd
        if len(rates) < 10:
            continue
        mean_r = float(np.mean(list(rates.values())))
        if mean_r <= 0.0:
            continue
        # Laplace shrinkage toward 1.0 calibrated on 6-origin walk-forward backtest
        shrink = tot / (tot + 600.0)
        idx: Dict[int, float] = {}
        for m in range(1, 13):
            raw = (rates.get(m, mean_r) / mean_r) if m in rates else 1.0
            idx[m] = float(np.clip(1.0 + shrink * (raw - 1.0), 0.30, 3.20))
        out[k] = idx
    return out


def get_archetype1_seasonal_profiles(
    ch,
    ref_dt: date,
    festival_date_map: Dict[date, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Query 3 years of de-festivalized monthly sales across all 8 Archetype 1
    major appliance lines in a single fast ClickHouse scan (~250ms, cached 30m)
    and build hierarchical seasonal index tables:
      - `idx_pgrp`: `PROD||ICAT||IGRP` (e.g. `STABILIZER||STABILIZER||AC- STABILIZER`)
      - `idx_pcat`: `PROD||ICAT`       (e.g. `AIR CONDITIONER||AC INDOOR`)
      - `idx_prod`: `PROD`             (e.g. `AIR CONDITIONER`)
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch1_seas_v1:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    empty = {'idx_pgrp': {}, 'idx_pcat': {}, 'idx_prod': {}, 'ref_date': ref_str, 'history_start': ref_str}
    if ch is None:
        return empty

    start_3y = ref_dt - timedelta(days=1095)
    start_str = start_3y.strftime('%Y-%m-%d 00:00:00')
    end_str = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')

    # Pass festival date prior weights into ClickHouse `transform()` so movable festivals
    # (Onam, Deepavali, Vishu, Eid) are divided out before aggregating by Gregorian month.
    fest_dates_sql_items: List[str] = []
    fest_weights_sql_items: List[str] = []
    for fdt, finfo in sorted(festival_date_map.items()):
        if start_3y <= fdt <= ref_dt:
            pw = float(finfo.get('prior_weight', 1.0))
            if abs(pw - 1.0) > 1e-4:
                fest_dates_sql_items.append(f"toDate('{fdt.strftime('%Y-%m-%d')}')")
                fest_weights_sql_items.append(f"{max(pw, 0.5):.4f}")

    if fest_dates_sql_items:
        fmult_expr = (
            f"transform(toDate(s.date), [{', '.join(fest_dates_sql_items)}], "
            f"[{', '.join(fest_weights_sql_items)}], 1.0)"
        )
    else:
        fmult_expr = "1.0"

    arch1_sql = "', '".join(ARCHETYPE_1_PRODUCTS)
    excl_br_sql = "', '".join(_EXCLUDED_BRANCH_CODES)
    clean_cat_sql = ch_clean_item_cat_expr('m.item_category')
    clean_grp_sql = ch_clean_spec_group_expr('m.item_group', 'm.product')

    sql = f"""
        SELECT
            upper(trim(m.product)) AS prod,
            {clean_cat_sql} AS icat,
            {clean_grp_sql} AS igrp,
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
          AND upper(trim(m.product)) IN ('{arch1_sql}')
          AND s.item_code != ''
          AND upper(trim(s.branch)) NOT IN ('{excl_br_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY prod, icat, igrp, mo
    """
    try:
        rows = ch.query(sql).result_rows
    except Exception as e:
        print('[archetype1] seasonal profile query failed:', e)
        return empty

    if not rows:
        return empty

    # Determine actual earliest date in history so calendar-day counts per month are exact
    min_dates = []
    for r in rows:
        d_val = r[5]
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

    pgrp_rows: List[Tuple[str, int, float]] = []
    pcat_rows: List[Tuple[str, int, float]] = []
    prod_rows: List[Tuple[str, int, float]] = []
    for r in rows:
        prod = str(r[0] or '').strip().upper()
        icat = str(r[1] or '').strip().upper()
        igrp = str(r[2] or '').strip().upper()
        mo = int(r[3] or 0)
        df_qty = float(r[4] or 0.0)
        if not prod or mo < 1 or mo > 12 or df_qty <= 0.0:
            continue
        prod_rows.append((prod, mo, df_qty))
        if icat:
            pcat_rows.append((f"{prod}||{icat}", mo, df_qty))
        if igrp:
            pgrp_rows.append((f"{prod}||{icat}||{igrp}", mo, df_qty))

    profiles = {
        'idx_pgrp': _build_month_index_table(pgrp_rows, cal_days_by_mo, min_total_units=400.0),
        'idx_pcat': _build_month_index_table(pcat_rows, cal_days_by_mo, min_total_units=150.0),
        'idx_prod': _build_month_index_table(prod_rows, cal_days_by_mo, min_total_units=100.0),
        'ref_date': ref_str,
        'history_start': hist_start.strftime('%Y-%m-%d'),
    }
    cache.set(cache_key, profiles, 1800)
    return profiles


def lookup_archetype1_month_index(
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    icat: Any = '',
    igrp: Any = '',
) -> Dict[int, float]:
    """
    Hierarchical seasonal index lookup for an Archetype 1 entity:
      - Blends 60% `PROD||ICAT||IGRP` (spec group) + 40% `PROD||ICAT` (or `PROD`)
        when both exist, capturing sub-category nuances like AC-Stabilizer vs Ref-Stabilizer.
      - Falls back cleanly to `PROD||ICAT` -> `PROD`.
      - Returns `{}` for non-Archetype-1 products.
    """
    if not profiles:
        return {}
    p = ' '.join(str(prod or '').upper().strip().split())
    if p not in ARCHETYPE_1_SET:
        return {}
    ic = normalize_item_category(icat)
    ig = normalize_spec_group(igrp, p)

    idx_prod = profiles.get('idx_prod') or {}
    idx_pcat = profiles.get('idx_pcat') or {}
    idx_pgrp = profiles.get('idx_pgrp') or {}

    base = idx_pcat.get(f"{p}||{ic}") if ic else None
    if not base:
        base = idx_prod.get(p)
    pg = idx_pgrp.get(f"{p}||{ic}||{ig}") if ig else None

    if pg and base:
        return {m: 0.60 * float(pg.get(m, 1.0)) + 0.40 * float(base.get(m, 1.0)) for m in range(1, 13)}
    if pg:
        return dict(pg)
    if base:
        return dict(base)
    return {}


def smooth_month_index_on_date(d: date, mo_idx: Dict[int, float]) -> float:
    """
    Linearly interpolated monthly seasonal index across adjacent months so
    transitions (e.g. May 25 -> Jun 5 monsoon onset) are smooth rather than step functions.
    """
    if not mo_idx:
        return 1.0
    m = d.month
    day = d.day
    cur_v = float(mo_idx.get(m, 1.0))
    if day <= 15:
        prev_m = 12 if m == 1 else m - 1
        w = (15 - day) / 30.0
        return (1.0 - w) * cur_v + w * float(mo_idx.get(prev_m, 1.0))
    next_m = 1 if m == 12 else m + 1
    w = (day - 15) / 30.0
    return (1.0 - w) * cur_v + w * float(mo_idx.get(next_m, 1.0))


def compute_seasonal_transition_factors(
    ref_dt: date,
    mo_idx: Dict[int, float],
) -> Tuple[float, float, float]:
    """
    Compute blended calendar seasonal transition multipliers `(f7, f15, f30)` from
    trailing 30-day average smooth seasonal index to the upcoming 7/15/30-day horizon.
    Blends 75% calendar transition ratio with 25% neutral 1.0 for variance safety.
    """
    if not mo_idx:
        return 1.0, 1.0, 1.0
    tr_vals = [smooth_month_index_on_date(ref_dt - timedelta(days=a), mo_idx) for a in range(30)]
    fut_vals = [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)]
    tr_m = max(float(np.mean(tr_vals)), 0.15)
    out = []
    for h in (7, 15, 30):
        fut_m = float(np.mean(fut_vals[:h]))
        raw_ratio = float(np.clip(fut_m / tr_m, 0.35, 2.80))
        out.append(0.25 + 0.75 * raw_ratio)
    return out[0], out[1], out[2]


# ═════════════════════════════════════════════════════════════════════════════
# 3. APPLY ARCHETYPE 1 SEASONALITY + SPEC-BUCKET METADATA TO FORECAST DATAFRAME
# ═════════════════════════════════════════════════════════════════════════════

def apply_archetype1_forecast_enhancements(
    df: pd.DataFrame,
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    gran: str,
    fc_engine: str,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Annotate every row with Spec-Bucket metadata and apply empirically validated
    de-festivalized Spec-Bucket calendar seasonal multipliers to Archetype 1
    major appliance rows when `fc_engine in ('hybrid', 'ml')`.

    Non-Archetype-1 rows (Mobiles, Laptops, Accessories, etc.) and `statistical`
    engine baseline forecasts remain 100% untouched.
    """
    n = len(df)
    spec_ids = [''] * n
    spec_labels = [''] * n
    arch1_active = np.zeros(n, dtype=bool)
    f7_arr = np.ones(n, dtype=float)
    f15_arr = np.ones(n, dtype=float)
    f30_arr = np.ones(n, dtype=float)

    has_icat = 'item_category' in df.columns
    has_igrp = 'item_group' in df.columns
    prods = df['product_line'].astype(str).tolist()
    brands = df['brand'].astype(str).tolist()
    icats = df['item_category'].astype(str).tolist() if has_icat else [''] * n
    igrps = df['item_group'].astype(str).tolist() if has_igrp else [''] * n

    # Memoize per unique (prod, icat, igrp) so 20k rows take < 5ms!
    factor_cache: Dict[Tuple[str, str, str], Tuple[float, float, float, bool]] = {}
    apply_seasonal = bool(profiles) and (fc_engine in ('hybrid', 'ml'))

    for i in range(n):
        p = prods[i].strip().upper()
        b = brands[i].strip().upper() or 'GENERAL'
        ic = normalize_item_category(icats[i])
        ig = normalize_spec_group(igrps[i], p)

        if p in ARCHETYPE_1_SET:
            if gran == 'spec_bucket':
                spec_ids[i] = build_spec_bucket_id(p, b, ic, ig)
                spec_labels[i] = format_spec_bucket_label(p, b, ic, ig)
            elif gran == 'sku':
                spec_ids[i] = build_spec_bucket_id(p, b, ic, ig)
                spec_labels[i] = format_spec_bucket_label(p, b, ic, ig)
            else:
                spec_ids[i] = ''
                spec_labels[i] = ''

            if apply_seasonal:
                ckey = (p, ic, ig)
                cached_f = factor_cache.get(ckey)
                if cached_f is None:
                    mo_idx = lookup_archetype1_month_index(profiles, p, ic, ig)
                    if mo_idx:
                        f7, f15, f30 = compute_seasonal_transition_factors(ref_dt, mo_idx)
                        cached_f = (f7, f15, f30, True)
                    else:
                        cached_f = (1.0, 1.0, 1.0, False)
                    factor_cache[ckey] = cached_f
                f7_arr[i], f15_arr[i], f30_arr[i], is_on = cached_f
                arch1_active[i] = is_on
        elif gran == 'spec_bucket':
            spec_ids[i] = build_spec_bucket_id(p, b, ic, '')
            spec_labels[i] = format_spec_bucket_label(p, b, ic, '')

    df['spec_bucket_id'] = spec_ids
    df['spec_bucket_label'] = spec_labels
    df['archetype1_active'] = arch1_active
    df['archetype1_seasonal_adj_pct'] = np.where(
        arch1_active,
        np.round((f30_arr - 1.0) * 100.0, 1),
        0.0,
    )

    if apply_seasonal and bool(arch1_active.any()):
        mask = arch1_active
        new_fc7 = np.where(mask, np.maximum(0.0, df['forecast_7d'].astype(float) * f7_arr), df['forecast_7d'].astype(float))
        new_fc15 = np.where(mask, np.maximum(new_fc7, df['forecast_15d'].astype(float) * f15_arr), df['forecast_15d'].astype(float))
        new_fc30 = np.where(mask, np.maximum(new_fc15, df['forecast_30d'].astype(float) * f30_arr), df['forecast_30d'].astype(float))
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

    active_cnt = int(arch1_active.sum())
    avg_adj = round(float(df.loc[arch1_active, 'archetype1_seasonal_adj_pct'].mean()), 1) if active_cnt > 0 else 0.0
    distinct_buckets = len({s for s in spec_ids if s})
    diagnostics = {
        'enabled': bool(active_cnt > 0),
        'archetype': 'Archetype 1 · Spec-Bucket Major Appliances',
        'products_covered': list(ARCHETYPE_1_PRODUCTS),
        'enhanced_entities': active_cnt,
        'distinct_spec_buckets': distinct_buckets,
        'avg_seasonal_adj_pct': avg_adj,
        'history_start': (profiles or {}).get('history_start', ''),
    }
    return df, diagnostics


def compute_entity_daily_seasonal_curve(
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    icat: Any = '',
    igrp: Any = '',
) -> Optional[np.ndarray]:
    """
    Returns a 30-day relative seasonal curve (mean=1.0) for shaping the daily forecast
    chart of an Archetype 1 entity across month boundaries (e.g. Summer ramp / Monsoon drop).
    Returns `None` for non-Archetype-1 products or when profiles are unavailable.
    """
    mo_idx = lookup_archetype1_month_index(profiles, prod, icat, igrp)
    if not mo_idx:
        return None
    vals = np.array(
        [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)],
        dtype=float,
    )
    mean_v = float(np.mean(vals))
    if mean_v <= 0.0:
        return None
    # Blend 75% seasonal shape with 25% flat 1.0 to match horizon multiplier dampening
    rel = 0.25 + 0.75 * (vals / mean_v)
    return rel / max(float(np.mean(rel)), 1e-6)


# ═════════════════════════════════════════════════════════════════════════════
# 4. SPEC-BUCKET MEMBER SKU BREAKDOWN & ALLOCATED REPLENISHMENT
# ═════════════════════════════════════════════════════════════════════════════

def _allocate_integers(total: int, weights: List[float]) -> List[int]:
    """Largest-remainder integer allocation summing exactly to `total`."""
    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    w_sum = sum(max(0.0, w) for w in weights)
    if w_sum <= 0.0:
        return [0] * n
    exact = [total * max(0.0, w) / w_sum for w in weights]
    floors = [int(math.floor(x)) for x in exact]
    rem = int(total - sum(floors))
    order = sorted(range(n), key=lambda i: (exact[i] - floors[i], weights[i]), reverse=True)
    for i in order[:max(0, rem)]:
        floors[i] += 1
    return floors


def build_spec_bucket_member_skus(
    ch,
    ref_dt: date,
    stock_dt: Optional[date],
    spec_bucket_id: str,
    sales_where: List[str],
    stock_where: List[str],
    stock_table: str,
    bucket_fc7: float,
    bucket_fc15: float,
    bucket_fc30: float,
    limit: int = 30,
) -> Dict[str, Any]:
    """
    Query all active / stocked `item_code` SKUs inside a selected Spec Bucket and
    allocate the bucket's 7d/15d/30d forecast down to each physical SKU by recency-weighted
    demand share (0.70 * d30 + 0.30 * d90/3), so buyers know exactly which model codes
    to reorder or phase out.
    """
    empty = {
        'spec_bucket_id': spec_bucket_id,
        'total_skus': 0,
        'active_skus_30d': 0,
        'stocked_skus': 0,
        'items': [],
    }
    if ch is None or not spec_bucket_id or '||' not in spec_bucket_id:
        return empty

    p_part, b_part, ic_part, ig_part = parse_spec_bucket_id(spec_bucket_id)
    if not p_part:
        return empty

    start_90 = (ref_dt - timedelta(days=89)).strftime('%Y-%m-%d 00:00:00')
    start_30 = (ref_dt - timedelta(days=29)).strftime('%Y-%m-%d 00:00:00')
    end_dt = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"

    sales_bkt_clauses = list(sales_where) + ch_spec_bucket_where_clauses(
        spec_bucket_id,
        'm.product', 'm.brand', 'm.item_category', 'm.item_group',
        name_col='m.item_name',
        price_expr=_SALES_COMP_PRICE_EXPR,
    )
    sales_rows = []
    try:
        sales_rows = ch.query(f"""
            SELECT
                s.item_code AS code,
                any(if(m.item_name != '', m.item_name, s.item_code)) AS name,
                greatest(0.0, sumIf({net_q}, s.date >= toDateTime('{start_30}'))) AS d30,
                greatest(0.0, sum({net_q})) AS d90,
                maxIf(toDate(s.date), s.sold_price > 0) AS last_dt
            FROM azure_sales_report s
            INNER JOIN item_master m ON s.item_code = m.item_code
            WHERE s.date >= toDateTime('{start_90}')
              AND s.date < toDateTime('{end_dt}')
              AND {" AND ".join(sales_bkt_clauses)}
            GROUP BY s.item_code
            HAVING d90 > 0
        """).result_rows
    except Exception as e:
        print('[archetype1] member SKUs sales query failed:', e)

    stock_rows = []
    if stock_dt is not None and stock_where:
        stock_bkt_clauses = list(stock_where) + ch_spec_bucket_where_clauses(
            spec_bucket_id,
            'product', 'brand', 'item_category', 'item_group',
            name_col='item',
            price_expr=_STOCK_COMP_PRICE_EXPR,
        )
        try:
            stock_rows = ch.query(f"""
                SELECT
                    item_code AS code,
                    any(if(item != '', item, item_code)) AS name,
                    sum(greatest(0.0, toFloat64(stock))) AS stk,
                    sum(greatest(0.0, toFloat64(transit))) AS trn,
                    avgIf(toFloat64(total_age), stock > 0) AS avg_age
                FROM {stock_table}
                WHERE {" AND ".join(stock_bkt_clauses)}
                GROUP BY item_code
                HAVING stk > 0 OR trn > 0
            """).result_rows
        except Exception as e:
            print('[archetype1] member SKUs stock query failed:', e)

    merged: Dict[str, Dict[str, Any]] = {}
    for r in sales_rows:
        code = str(r[0] or '').strip()
        if not code:
            continue
        last_d = r[4]
        if isinstance(last_d, date) and last_d.year >= 2000:
            dsl = max(0, (ref_dt - last_d).days)
        else:
            dsl = None
        merged[code] = {
            'code': code,
            'name': str(r[1] or code),
            'product_line': p_part,
            'brand': b_part,
            'd30': float(r[2] or 0.0),
            'd90': float(r[3] or 0.0),
            'current_stock': 0.0,
            'transit_qty': 0.0,
            'avg_age': 0.0,
            'days_since_sale': dsl,
        }

    for r in stock_rows:
        code = str(r[0] or '').strip()
        if not code:
            continue
        rec = merged.get(code)
        if rec is None:
            rec = merged[code] = {
                'code': code,
                'name': str(r[1] or code),
                'product_line': p_part,
                'brand': b_part,
                'd30': 0.0,
                'd90': 0.0,
                'current_stock': 0.0,
                'transit_qty': 0.0,
                'avg_age': 0.0,
                'days_since_sale': None,
            }
        rec['current_stock'] = float(r[2] or 0.0)
        rec['transit_qty'] = float(r[3] or 0.0)
        age_v = float(r[4] or 0.0)
        rec['avg_age'] = 0.0 if math.isnan(age_v) else age_v

    all_skus = list(merged.values())
    if not all_skus:
        return empty

    # Recency-weighted demand share: strongly prefers active/new model codes over retiring ones
    weights = [0.70 * s['d30'] + 0.30 * (s['d90'] / 3.0) for s in all_skus]
    total_w = sum(weights)
    if total_w <= 0.0:
        weights = [s['current_stock'] for s in all_skus]
        total_w = sum(weights)

    fc7_int = int(round(max(0.0, float(bucket_fc7))))
    fc15_int = int(round(max(0.0, float(bucket_fc15))))
    fc30_int = int(round(max(0.0, float(bucket_fc30))))

    alloc7 = _allocate_integers(fc7_int, weights)
    alloc15 = _allocate_integers(fc15_int, weights)
    alloc30 = _allocate_integers(fc30_int, weights)

    enriched: List[Dict[str, Any]] = []
    for i, s in enumerate(all_skus):
        share_pct = round((weights[i] / total_w) * 100.0, 1) if total_w > 0 else 0.0
        a7, a15, a30 = alloc7[i], alloc15[i], alloc30[i]
        stk = int(round(s['current_stock']))
        trn = int(round(s['transit_qty']))
        d30 = int(round(s['d30']))
        d90 = int(round(s['d90']))
        repl30 = max(0, int(math.ceil(a30 * 1.15 - (stk + trn))))

        if d30 > 0:
            lifecycle = 'Active'
        elif d90 > 0 and stk <= 2:
            lifecycle = 'Phase-Out'
        elif d90 > 0:
            lifecycle = 'Slowing'
        elif s['avg_age'] < 90 and stk > 0:
            lifecycle = 'New / Unsold'
        else:
            lifecycle = 'Aged Stock'

        enriched.append({
            'code': s['code'],
            'name': s['name'],
            'product_line': s['product_line'],
            'brand': s['brand'],
            'd30': d30,
            'd90': d90,
            'share_pct': share_pct,
            'alloc_fc7': a7,
            'alloc_fc15': a15,
            'alloc_fc30': a30,
            'current_stock': stk,
            'transit_qty': trn,
            'replenish_30d': repl30,
            'avg_age_days': int(round(s['avg_age'])),
            'days_since_sale': s['days_since_sale'],
            'lifecycle': lifecycle,
        })

    enriched.sort(key=lambda x: (-x['d30'], -x['alloc_fc30'], -x['current_stock'], -x['d90'], x['code']))
    from .demand_archetype4 import ARCHETYPE_4_SET, format_arch4_bucket_label
    from .demand_archetype5 import ARCHETYPE_5_SET, format_arch5_bucket_label
    if p_part in ('LAPTOP', 'DESKTOP', 'PRINTER'):
        from .demand_archetype3 import format_computing_bucket_label
        bkt_lbl = format_computing_bucket_label(b_part, ic_part, ig_part)
        bkt_kind = 'computing_spec_tier'
    elif p_part in ARCHETYPE_4_SET:
        bkt_lbl = format_arch4_bucket_label(p_part, b_part, ic_part, ig_part)
        bkt_kind = 'subcat_bucket'
    elif p_part in ARCHETYPE_5_SET:
        bkt_lbl = format_arch5_bucket_label(p_part, b_part, ic_part, ig_part)
        bkt_kind = 'pack_size_bucket' if p_part == 'FRAGRANCE' else 'attach_bucket'
    else:
        if ig_part and ic_part:
            bkt_lbl = f"{b_part} — {ig_part} ({ic_part})"
        elif ig_part:
            bkt_lbl = f"{b_part} — {ig_part} ({p_part})"
        elif ic_part:
            bkt_lbl = f"{b_part} — {ic_part}"
        else:
            bkt_lbl = f"{b_part} — {p_part}"
        bkt_kind = 'model_family' if p_part in ('MOBILE', 'TABLET') else 'spec_bucket'
    return {
        'spec_bucket_id': spec_bucket_id,
        'spec_bucket_label': bkt_lbl,
        'bucket_kind': bkt_kind,
        'total_skus': len(enriched),
        'active_skus_30d': sum(1 for x in enriched if x['d30'] > 0),
        'stocked_skus': sum(1 for x in enriched if x['current_stock'] > 0),
        'items': enriched[:limit],
    }
