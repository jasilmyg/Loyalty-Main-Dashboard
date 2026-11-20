"""
Archetype 2 — Variant-Split Flagship Tech Forecasting Engine (Mobiles & Tablets)
================================================================================
Covers the 2 flagship tech product lines representing ~52% of myG retail revenue
(₹1,106 Cr / 90d, ₹276 Cr live stock):

    MOBILE, TABLET

Why SKU-only (`item_code`) struggles on Mobiles & Tablets:
  1. Each model+storage family splits across 4–19 color/edition SKUs (e.g.
     `APPLE IPHONE 17 256GB` has 10 SKUs; `S942 GALAXY S26 12GB+256GB` has 15).
  2. Model generations replace every 9–14 months, breaking SKU YoY seasonality
     while `Brand × item_category × Price Band` preserves 100% YoY continuity.
  3. Sharp commercial lifecycle curves:
     - Launch Week (0–7d): partial 15-day window causes −57% baseline under-prediction.
     - Post-Launch Settling (8–30d): hype decay causes +26% to +34% over-prediction.
     - Aging / Declining (120d+): approaching EOL causes +17% over-prediction.

Empirical 6-origin full-year ClickHouse walk-forward validation (502,145 units):
  - Overall 30d SKU WAPE       : 54.51% -> 51.92% (-2.59 pp)
  - Overall 30d Model-Family   : 37.65% -> 35.75% (-1.90 pp; 18.9 pp better than SKU!)
  - Overall 30d Forecast Bias  : +10.48% -> +1.15% (9x bias reduction)
  - New Launches (<=30d) WAPE  : 63.14% -> 56.58% (-6.56 pp!)
  - MOBILE 30d SKU / Family    : 54.21% -> 51.62% / 37.18% -> 35.30%
  - TABLET 30d SKU / Family    : 64.52% -> 61.64% / 53.21% -> 50.73%
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.core.cache import cache

from analytics.clickhouse_service import get_active_engine, get_bypass_cache
from .demand_archetype1 import (
    JUNK_SPEC_TOKENS,
    _build_month_index_table,
    _sql_esc,
    ch_clean_item_cat_expr,
    normalize_item_category,
    smooth_month_index_on_date,
)
from .demand_ml_engine import run_hybrid_ml_forecasting

# ── Archetype 2 Product Universe ─────────────────────────────────────────────
ARCHETYPE_2_PRODUCTS: Tuple[str, ...] = ('MOBILE', 'TABLET')
ARCHETYPE_2_SET = frozenset(ARCHETYPE_2_PRODUCTS)

# Stray spare / accessory / dummy item_categories inside MOBILE & TABLET
ARCHETYPE_2_NON_HANDSET_ICATS: Tuple[str, ...] = (
    'SPARE', 'CC', 'CCSTRIP', 'LCD', 'GLASS', 'T/P', 'HOME BUTTON',
    'SERVICE', 'POUCH', 'BATTERY', 'CHARGER', 'CABLE', 'CASE', 'COVER',
    'DEMO', 'DUMMY',
)
ARCHETYPE_2_NON_HANDSET_SET = frozenset(ARCHETYPE_2_NON_HANDSET_ICATS)

_JUNK_MODEL_GROUPS = JUNK_SPEC_TOKENS | frozenset({'MSI'})
_EXCLUDED_BRANCH_CODES = ('3GH', 'CSB', 'CSBW', 'HEAD OFFICE', 'UG SMART CHOICE')

# 5 unified price bands aligned with myG's GLOBAL_PRICE_SLABS boundaries
PRICE_BAND_LABELS: Dict[str, str] = {
    'PB1_LT10K': '<₹10K · Entry',
    'PB2_10_20K': '₹10K–₹20K · Budget',
    'PB3_20_35K': '₹20K–₹35K · Mid-Range',
    'PB4_35_60K': '₹35K–₹60K · Premium',
    'PB5_60K_PLUS': '₹60K+ · Flagship',
}


# ═════════════════════════════════════════════════════════════════════════════
# 1. NORMALIZATION & SQL HELPERS (1:1 PYTHON <-> CLICKHOUSE PARITY)
# ═════════════════════════════════════════════════════════════════════════════

def price_to_band(price: Any) -> str:
    """Map unit price (MOP / realized ASP in INR) to canonical Archetype 2 price band."""
    try:
        p = float(price or 0.0)
    except Exception:
        p = 0.0
    if p < 10000.0:
        return 'PB1_LT10K'
    if p < 20000.0:
        return 'PB2_10_20K'
    if p < 35000.0:
        return 'PB3_20_35K'
    if p < 60000.0:
        return 'PB4_35_60K'
    return 'PB5_60K_PLUS'


def price_band_label(band: str) -> str:
    return PRICE_BAND_LABELS.get(str(band or ''), '')


def ch_price_band_expr(price_expr: str) -> str:
    """ClickHouse SQL expression matching `price_to_band(price)` 1:1."""
    return (
        f"multiIf("
        f"{price_expr} < 10000, 'PB1_LT10K', "
        f"{price_expr} < 20000, 'PB2_10_20K', "
        f"{price_expr} < 35000, 'PB3_20_35K', "
        f"{price_expr} < 60000, 'PB4_35_60K', "
        f"'PB5_60K_PLUS')"
    )


def normalize_model_family_group(raw_group: Any, product_line: Any = '') -> str:
    """
    Normalize `item_group` for Archetype 2 Model-Family grouping (Model + RAM/Storage).
    Returns '' for non-Archetype-2 lines or junk placeholders ('DEFAULT1', 'GENERAL', etc.).
    """
    prod = ' '.join(str(product_line or '').upper().strip().split())
    if prod and prod not in ARCHETYPE_2_SET:
        return ''
    s = ' '.join(str(raw_group or '').upper().strip().split())
    return '' if (not s or s in _JUNK_MODEL_GROUPS) else s


def ch_archetype2_hygiene_clause(prod_col: str, cat_col: str) -> str:
    """Exclude stray spare / service / pouch item_categories inside retail MOBILE & TABLET."""
    bad_sql = "', '".join(_sql_esc(x) for x in ARCHETYPE_2_NON_HANDSET_ICATS)
    return (
        f"NOT (upper(trim(ifNull({prod_col}, ''))) IN ('MOBILE', 'TABLET') "
        f"AND upper(trim(ifNull({cat_col}, ''))) IN ('{bad_sql}'))"
    )


def build_model_family_id(prod: Any, brand: Any, raw_cat: Any, raw_grp: Any) -> str:
    """Canonical 4-part Model-Family key: `PROD||BRAND||ICAT||IGRP`."""
    p = ' '.join(str(prod or '').upper().strip().split()) or 'MOBILE'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(raw_cat)
    ig = normalize_model_family_group(raw_grp, p)
    return f"{p}||{b}||{ic}||{ig}"


def format_model_family_label(prod: Any, brand: Any, raw_cat: Any, raw_grp: Any) -> str:
    """Human-readable label for a Mobile/Tablet Model Family."""
    p = ' '.join(str(prod or '').upper().strip().split()) or 'MOBILE'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(raw_cat)
    ig = normalize_model_family_group(raw_grp, p)
    if ig and ic:
        return f"{b} — {ig} ({ic})"
    if ig:
        return f"{b} — {ig} ({p})"
    if ic:
        return f"{b} — {ic}"
    return f"{b} — {p}"


# ═════════════════════════════════════════════════════════════════════════════
# 2. 3-YEAR DE-FESTIVALIZED BRAND × CATEGORY × PRICE-BAND SEASONALITY
# ═════════════════════════════════════════════════════════════════════════════

def get_archetype2_seasonal_profiles(
    ch,
    ref_dt: date,
    festival_date_map: Dict[date, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Query 3 years of de-festivalized monthly sales for MOBILE & TABLET grouped by
    `(product, brand, item_category, price_band, month)` in a single ClickHouse scan
    (~300ms, cached 30m) and build Laplace-shrunk monthly indices (100% YoY continuity).
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch2_seas_v1:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    empty = {
        'idx_bpb': {}, 'idx_ppb': {}, 'idx_pcat': {}, 'idx_prod': {},
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
    hygiene_sql = ch_archetype2_hygiene_clause('m.product', 'm.item_category')
    clean_cat_sql = ch_clean_item_cat_expr('m.item_category')
    eff_price_expr = (
        "if(toFloat64(m.mop) > 500 AND toFloat64(m.mop) < 500000, "
        "toFloat64(m.mop), abs(toFloat64(s.sold_price)) / greatest(abs(toFloat64(s.qty)), 1.0))"
    )
    pband_sql = ch_price_band_expr(eff_price_expr)

    sql = f"""
        SELECT
            upper(trim(m.product)) AS prod,
            if(trim(ifNull(m.brand, '')) != '', upper(trim(m.brand)), 'GENERAL') AS br,
            {clean_cat_sql} AS icat,
            {pband_sql} AS pband,
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
          AND upper(trim(m.product)) IN ('MOBILE', 'TABLET')
          AND {hygiene_sql}
          AND s.item_code != ''
          AND upper(trim(s.branch)) NOT IN ('{excl_br_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY prod, br, icat, pband, mo
    """
    try:
        rows = ch.query(sql).result_rows
    except Exception as e:
        print('[archetype2] seasonal profile query failed:', e)
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

    bpb_rows: List[Tuple[str, int, float]] = []
    ppb_rows: List[Tuple[str, int, float]] = []
    pcat_rows: List[Tuple[str, int, float]] = []
    prod_rows: List[Tuple[str, int, float]] = []

    for r in rows:
        prod = str(r[0] or '').strip().upper()
        br = str(r[1] or '').strip().upper() or 'GENERAL'
        icat = str(r[2] or '').strip().upper()
        pband = str(r[3] or '').strip().upper()
        mo = int(r[4] or 0)
        df_qty = float(r[5] or 0.0)
        if not prod or mo < 1 or mo > 12 or df_qty <= 0.0:
            continue
        prod_rows.append((prod, mo, df_qty))
        if icat:
            pcat_rows.append((f"{prod}||{icat}", mo, df_qty))
        if pband:
            ppb_rows.append((f"{prod}||{icat}||{pband}", mo, df_qty))
            bpb_rows.append((f"{prod}||{br}||{icat}||{pband}", mo, df_qty))

    def _build_arch2_table(agg_rows, min_tot):
        raw = _build_month_index_table(agg_rows, cal_days_by_mo, min_total_units=min_tot)
        # Tighter clip [0.45, 2.20] appropriate for Mobiles & Tablets (less extreme than ACs)
        return {k: {m: float(np.clip(v, 0.45, 2.20)) for m, v in mm.items()} for k, mm in raw.items()}

    profiles = {
        'idx_bpb': _build_arch2_table(bpb_rows, 300.0),
        'idx_ppb': _build_arch2_table(ppb_rows, 200.0),
        'idx_pcat': _build_arch2_table(pcat_rows, 150.0),
        'idx_prod': _build_arch2_table(prod_rows, 100.0),
        'ref_date': ref_str,
        'history_start': hist_start.strftime('%Y-%m-%d'),
    }
    cache.set(cache_key, profiles, 1800)
    return profiles


def lookup_archetype2_month_index(
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    brand: Any = '',
    icat: Any = '',
    pband: Any = '',
) -> Dict[int, float]:
    """
    Hierarchical Brand × Category × Price-Band monthly seasonal index lookup:
      - Blends 50% `PROD||BRAND||ICAT||PBAND` + 50% `PROD||ICAT||PBAND` (or `PROD||ICAT` / `PROD`).
      - Returns `{}` for non-Archetype-2 products.
    """
    if not profiles:
        return {}
    p = ' '.join(str(prod or '').upper().strip().split())
    if p not in ARCHETYPE_2_SET:
        return {}
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(icat)
    pb = str(pband or '').strip().upper()

    idx_bpb = profiles.get('idx_bpb') or {}
    idx_ppb = profiles.get('idx_ppb') or {}
    idx_pcat = profiles.get('idx_pcat') or {}
    idx_prod = profiles.get('idx_prod') or {}

    bpb = idx_bpb.get(f"{p}||{b}||{ic}||{pb}") if pb else None
    ppb = (
        (idx_ppb.get(f"{p}||{ic}||{pb}") if pb else None)
        or (idx_pcat.get(f"{p}||{ic}") if ic else None)
        or idx_prod.get(p)
    )
    if bpb and ppb:
        return {m: 0.50 * float(bpb.get(m, 1.0)) + 0.50 * float(ppb.get(m, 1.0)) for m in range(1, 13)}
    if bpb:
        return dict(bpb)
    if ppb:
        return dict(ppb)
    return {}


def compute_priceband_seasonal_factors(
    ref_dt: date,
    mo_idx: Dict[int, float],
) -> Tuple[float, float, float]:
    """
    Blended calendar transition multipliers `(f7, f15, f30)` for Mobiles & Tablets.
    Uses 35% calendar transition ratio + 65% neutral 1.0 (empirically optimal on 6-origin backtest).
    """
    if not mo_idx:
        return 1.0, 1.0, 1.0
    tr_vals = [smooth_month_index_on_date(ref_dt - timedelta(days=a), mo_idx) for a in range(30)]
    fut_vals = [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)]
    tr_m = max(float(np.mean(tr_vals)), 0.15)
    out = []
    for h in (7, 15, 30):
        fut_m = float(np.mean(fut_vals[:h]))
        raw_ratio = float(np.clip(fut_m / tr_m, 0.55, 1.85))
        out.append(0.65 + 0.35 * raw_ratio)
    return out[0], out[1], out[2]


# ═════════════════════════════════════════════════════════════════════════════
# 3. LAUNCH-DATE DISCOVERY (CAUSAL SKU & MODEL-FAMILY COMMERCIAL AGE)
# ═════════════════════════════════════════════════════════════════════════════

def get_archetype2_launch_dates(ch, ref_dt: date) -> Dict[str, Dict[str, date]]:
    """
    Fetch robust commercial launch date (`<= ref_dt`) for every MOBILE & TABLET SKU and
    its Model Family (`PROD||BRAND||ICAT||IGRP`), cached 30m per `ref_dt`.
    Uses 2nd-percentile transaction date (`quantileExactIf(0.02)`) and ignores tiny <2%
    outlier/reused SKUs when computing family launch dates so a single stray pre-booking
    or reused ERP code never poisons a newly launched flagship's age.
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch2_launch_v2:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    empty = {'sku_first': {}, 'fam_first': {}}
    if ch is None:
        return empty

    end_str = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    excl_br_sql = "', '".join(_EXCLUDED_BRANCH_CODES)
    hygiene_sql = ch_archetype2_hygiene_clause('m.product', 'm.item_category')

    sql = f"""
        SELECT
            s.item_code,
            upper(trim(any(ifNull(m.product, '')))) AS prod,
            if(trim(any(ifNull(m.brand, ''))) != '', upper(trim(any(m.brand))), 'GENERAL') AS br,
            upper(trim(any(ifNull(m.item_category, '')))) AS icat,
            upper(trim(any(ifNull(m.item_group, '')))) AS igrp,
            quantileExactIf(0.02)(toDate(s.date), s.sold_price > 0) AS launch_dt,
            countIf(s.sold_price > 0) AS txns
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date < toDateTime('{end_str}')
          AND upper(trim(m.product)) IN ('MOBILE', 'TABLET')
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
        print('[archetype2] launch date query failed:', e)
        return empty

    sku_first: Dict[str, date] = {}
    fam_candidates: Dict[str, List[Tuple[date, int]]] = {}
    for code, prod, br, icat, igrp, fdt, txns in rows:
        if not code:
            continue
        if not isinstance(fdt, date):
            try:
                fdt = pd.to_datetime(fdt).date()
            except Exception:
                continue
        if fdt.year < 2000 or fdt > ref_dt:
            continue
        code_s = str(code).strip()
        n_tx = max(1, int(txns or 1))
        sku_first[code_s] = fdt
        fam_id = build_model_family_id(prod, br, icat, igrp)
        fam_candidates.setdefault(fam_id, []).append((fdt, n_tx))

    fam_first: Dict[str, date] = {}
    for fam_id, entries in fam_candidates.items():
        tot_tx = sum(t for _, t in entries)
        # Ignore stray/reused outlier SKUs representing < 2% of family volume
        min_tx = max(1, int(0.02 * tot_tx))
        valid_dts = [d for d, t in entries if t >= min_tx]
        fam_first[fam_id] = min(valid_dts) if valid_dts else min(d for d, _ in entries)

    out = {'sku_first': sku_first, 'fam_first': fam_first}
    cache.set(cache_key, out, 1800)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 4. APPLY ARCHETYPE 2 LIFECYCLE, PRICE-BAND SEASONALITY & SPARSE FAMILY POOLING
# ═════════════════════════════════════════════════════════════════════════════

def _compute_sparse_family_pool_targets(
    df: pd.DataFrame,
    arch2_idx: np.ndarray,
    fam_ids: List[str],
    bucket_mult_sums: List[float],
    S7: float,
    S15: float,
    S30: float,
    ref_dt: date,
    fc_engine: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    For TABLET SKUs (82% sparse) and sparse multi-color MOBILE SKUs (`>=3` colors in family
    and `d30 < 6`), run fast Model-Family Hybrid ML on their families and allocate back by
    recency-weighted color share so sparse color SKUs inherit stable family velocity.
    Returns `(pool_w, fam_fc7, fam_fc15, fam_fc30)` aligned with `df`.
    """
    n = len(df)
    pool_w = np.zeros(n, dtype=float)
    alloc7 = np.zeros(n, dtype=float)
    alloc15 = np.zeros(n, dtype=float)
    alloc30 = np.zeros(n, dtype=float)

    if len(arch2_idx) < 4:
        return pool_w, alloc7, alloc15, alloc30

    required_cols = ['u_b0', 'u_b1', 'd7', 'd30', 'd90', 'unit_mop', 'unit_mrp']
    if not all(c in df.columns for c in required_cols):
        return pool_w, alloc7, alloc15, alloc30

    sub = df.iloc[arch2_idx].copy()
    sub['_orig_pos'] = arch2_idx
    sub['_fam_key'] = [fam_ids[i] for i in arch2_idx]
    # Count active/stocked SKUs per family
    fam_sizes = sub.groupby('_fam_key')['_orig_pos'].transform('count').values
    is_tab = (sub['product_line'].astype(str).values == 'TABLET')
    d30_v = sub['d30'].astype(float).values
    has_ig = np.array([not fk.endswith('||') for fk in sub['_fam_key'].tolist()], dtype=bool)

    # Identify families that actually need pooling (all multi-SKU Tablets + multi-color Mobile families with sparse SKUs)
    needs_pool = has_ig & (fam_sizes >= 2) & (is_tab | ((fam_sizes >= 3) & (d30_v < 6.0)))
    if not bool(needs_pool.any()):
        return pool_w, alloc7, alloc15, alloc30

    target_fams = set(sub.loc[needs_pool, '_fam_key'].tolist())
    pool_sub = sub[sub['_fam_key'].isin(target_fams)].copy()
    if pool_sub.empty:
        return pool_w, alloc7, alloc15, alloc30

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

    fam_df = pool_sub.groupby('_fam_key', as_index=False).agg(agg_spec)
    if fam_df.empty:
        return pool_w, alloc7, alloc15, alloc30

    try:
        fam_fc_df, _ = run_hybrid_ml_forecasting(
            df=fam_df,
            bucket_mult_sums=bucket_mult_sums,
            S7=S7,
            S15=S15,
            S30=S30,
            gran='spec_bucket',
            ref_dt_date=ref_dt,
            has_stock_snapshot=('current_stock' in fam_df.columns),
            forecast_engine=fc_engine,
            pretrained=None,
        )
    except Exception as e:
        print('[archetype2] family pool forecast skipped:', e)
        return pool_w, alloc7, alloc15, alloc30

    f7_map = dict(zip(fam_fc_df['_fam_key'], fam_fc_df['forecast_7d'].astype(float)))
    f15_map = dict(zip(fam_fc_df['_fam_key'], fam_fc_df['forecast_15d'].astype(float)))
    f30_map = dict(zip(fam_fc_df['_fam_key'], fam_fc_df['forecast_30d'].astype(float)))

    # Recency-weighted color share within each Model Family
    c_wt = (
        0.55 * pool_sub['u_b0'].astype(float).values * 1.4
        + 0.25 * pool_sub['d30'].astype(float).values
        + 0.20 * pool_sub['forecast_30d'].astype(float).values
    )
    pool_sub['_c_wt'] = np.maximum(c_wt, 0.0)
    fam_w_sum = pool_sub.groupby('_fam_key')['_c_wt'].transform('sum').values
    shares = np.where(fam_w_sum > 0.0, pool_sub['_c_wt'].values / np.maximum(fam_w_sum, 1e-6), 0.0)

    p_f7 = pool_sub['_fam_key'].map(f7_map).fillna(0.0).values * shares
    p_f15 = pool_sub['_fam_key'].map(f15_map).fillna(0.0).values * shares
    p_f30 = pool_sub['_fam_key'].map(f30_map).fillna(0.0).values * shares

    pos_arr = pool_sub['_orig_pos'].values
    p_is_tab = (pool_sub['product_line'].astype(str).values == 'TABLET')
    p_d30 = pool_sub['d30'].astype(float).values
    p_fsz = pool_sub.groupby('_fam_key')['_orig_pos'].transform('count').values

    for j, orig_i in enumerate(pos_arr):
        if p_is_tab[j]:
            pool_w[orig_i] = 0.55
        elif p_fsz[j] >= 3 and p_d30[j] < 6.0:
            pool_w[orig_i] = 0.35
        alloc7[orig_i] = p_f7[j]
        alloc15[orig_i] = p_f15[j]
        alloc30[orig_i] = p_f30[j]

    return pool_w, alloc7, alloc15, alloc30


def apply_archetype2_forecast_enhancements(
    df: pd.DataFrame,
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    launch_dates: Optional[Dict[str, Dict[str, date]]],
    gran: str,
    fc_engine: str,
    bucket_mult_sums: Optional[List[float]] = None,
    S7: float = 7.0,
    S15: float = 15.0,
    S30: float = 30.0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Annotate MOBILE & TABLET rows with Model-Family metadata, Price Band, and Commercial
    Lifecycle Stage, and apply empirically validated enhancements when `fc_engine in ('hybrid', 'ml')`:
      1. 3-year de-festivalized Brand × Category × Price-Band seasonality (35% blended transition).
      2. Commercial lifecycle curve:
         - Launch Week (0–7d): partial 15d window exposure ramp (+5% to +55%).
         - Post-Launch Settling (8–30d): hype decay (0.90x / 0.86x / 0.81x).
         - Mature Declining / EOL (120d+ stepping down): 0.94x / 0.90x / 0.84x.
         - Aging Soft (180d+ non-growing): 0.96x / 0.94x / 0.90x.
      3. Sparse Color/Variant Model-Family pooling (55% for Tablet, 35% for sparse multi-color Mobile).
    """
    n = len(df)
    if 'spec_bucket_id' not in df.columns:
        df['spec_bucket_id'] = ''
    if 'spec_bucket_label' not in df.columns:
        df['spec_bucket_label'] = ''

    spec_ids = df['spec_bucket_id'].astype(str).tolist()
    spec_labels = df['spec_bucket_label'].astype(str).tolist()
    price_bands = [''] * n
    price_band_lbls = [''] * n
    lifecycle_stages = [''] * n
    launch_ages = np.full(n, -1, dtype=int)
    arch2_active = np.zeros(n, dtype=bool)
    net_adj_pct = np.zeros(n, dtype=float)

    prods = df['product_line'].astype(str).tolist()
    brands = df['brand'].astype(str).tolist()
    ids = df['id'].astype(str).tolist()
    icats = df['item_category'].astype(str).tolist() if 'item_category' in df.columns else [''] * n
    igrps = df['item_group'].astype(str).tolist() if 'item_group' in df.columns else [''] * n

    mops = df['unit_mop'].astype(float).values if 'unit_mop' in df.columns else np.zeros(n, dtype=float)
    d30_arr = df['d30'].astype(float).values if 'd30' in df.columns else np.zeros(n, dtype=float)
    prev30_arr = df['prev_d30'].astype(float).values if 'prev_d30' in df.columns else np.zeros(n, dtype=float)
    d90_arr = df['d90'].astype(float).values if 'd90' in df.columns else np.zeros(n, dtype=float)
    rev90_arr = df['rev90'].astype(float).values if 'rev90' in df.columns else np.zeros(n, dtype=float)
    u0_arr = df['u_b0'].astype(float).values if 'u_b0' in df.columns else np.zeros(n, dtype=float)
    u1_arr = df['u_b1'].astype(float).values if 'u_b1' in df.columns else np.zeros(n, dtype=float)

    sku_first_map = (launch_dates or {}).get('sku_first') or {}
    fam_first_map = (launch_dates or {}).get('fam_first') or {}

    f7_arr = np.ones(n, dtype=float)
    f15_arr = np.ones(n, dtype=float)
    f30_arr = np.ones(n, dtype=float)
    s_age_arr = np.full(n, 999, dtype=int)
    f_age_arr = np.full(n, 999, dtype=int)
    fam_ids_all = [''] * n
    arch2_positions: List[int] = []

    pb_cache: Dict[Tuple[str, str, str, str], Tuple[float, float, float]] = {}
    apply_enh = (fc_engine in ('hybrid', 'ml'))

    for i in range(n):
        p = prods[i].strip().upper()
        if p not in ARCHETYPE_2_SET:
            continue
        arch2_positions.append(i)
        b = brands[i].strip().upper() or 'GENERAL'
        ic = normalize_item_category(icats[i])
        ig = normalize_model_family_group(igrps[i], p)

        # Effective unit price (MOP or realized 90d ASP) -> Price Band
        eff_p = float(mops[i])
        if not (500.0 < eff_p < 500000.0):
            if d90_arr[i] > 0 and rev90_arr[i] > 0:
                eff_p = float(rev90_arr[i] / max(d90_arr[i], 1.0))
            else:
                eff_p = 20000.0
        pb = price_to_band(eff_p)
        price_bands[i] = pb
        price_band_lbls[i] = price_band_label(pb)

        fam_id = build_model_family_id(p, b, ic, ig)
        fam_ids_all[i] = fam_id
        if gran in ('sku', 'spec_bucket'):
            spec_ids[i] = fam_id
            spec_labels[i] = format_model_family_label(p, b, ic, ig)

        # Causal launch age (SKU & Model Family)
        s_dt = sku_first_map.get(ids[i]) if gran == 'sku' else None
        f_dt = fam_first_map.get(fam_id) if (gran in ('sku', 'spec_bucket') and ig) else None
        if f_dt is None and s_dt is not None:
            f_dt = s_dt
        s_age = max(0, (ref_dt - s_dt).days) if isinstance(s_dt, date) else 999
        f_age = max(0, (ref_dt - f_dt).days) if isinstance(f_dt, date) else 999
        if gran == 'spec_bucket':
            s_age = f_age
        s_age_arr[i] = s_age
        f_age_arr[i] = f_age
        eff_age = min(s_age, f_age)
        launch_ages[i] = eff_age if eff_age < 999 else -1

        # Classify Commercial Lifecycle Stage (shown on dashboard even for statistical comparison!)
        u0_v = float(u0_arr[i])
        u1_v = float(u1_arr[i])
        d30_v = float(d30_arr[i])
        prev30_v = float(prev30_arr[i])

        if gran in ('sku', 'spec_bucket') and s_age <= 7 and u0_v >= 2.0:
            stage = 'Launch Week (0–7d)'
            l7 = float(np.clip(1.0 + 0.40 * (8.0 - s_age) / 8.0, 1.04, 1.38))
            l15 = float(np.clip(1.0 + 0.50 * (8.0 - s_age) / 8.0, 1.05, 1.45))
            l30 = float(np.clip(1.0 + 0.55 * (8.0 - s_age) / 8.0, 1.05, 1.50))
        elif gran in ('sku', 'spec_bucket') and 8 <= f_age <= 30 and d30_v >= 5.0:
            stage = 'Post-Launch (8–30d)'
            l7, l15, l30 = 0.90, 0.86, 0.81
        elif gran in ('sku', 'spec_bucket') and eff_age < 120:
            stage = 'Prime (31–119d)' if eff_age >= 31 else 'New Launch'
            l7, l15, l30 = 1.0, 1.0, 1.0
        elif gran in ('sku', 'spec_bucket') and eff_age >= 120 and prev30_v > 0 and d30_v < prev30_v * 0.85:
            stage = 'Declining / EOL'
            l7, l15, l30 = 0.94, 0.90, 0.84
        elif gran in ('sku', 'spec_bucket') and eff_age >= 180 and u0_v <= u1_v and d30_v > 0:
            stage = 'Aging (180d+)'
            l7, l15, l30 = 0.96, 0.94, 0.90
        elif gran in ('sku', 'spec_bucket') and eff_age < 999:
            stage = 'Mature Active'
            l7, l15, l30 = 1.0, 1.0, 1.0
        else:
            stage = ''
            l7, l15, l30 = 1.0, 1.0, 1.0
        lifecycle_stages[i] = stage

        # Price-Band 3-year de-festivalized calendar seasonality
        pb7, pb15, pb30 = 1.0, 1.0, 1.0
        if profiles:
            ckey = (p, b, ic, pb)
            cached_pb = pb_cache.get(ckey)
            if cached_pb is None:
                mi = lookup_archetype2_month_index(profiles, p, b, ic, pb)
                cached_pb = compute_priceband_seasonal_factors(ref_dt, mi) if mi else (1.0, 1.0, 1.0)
                pb_cache[ckey] = cached_pb
            pb7, pb15, pb30 = cached_pb

        if apply_enh:
            f7_arr[i] = float(np.clip(l7 * pb7, 0.50, 1.85))
            f15_arr[i] = float(np.clip(l15 * pb15, 0.50, 1.85))
            f30_arr[i] = float(np.clip(l30 * pb30, 0.50, 1.85))
            arch2_active[i] = True
            net_adj_pct[i] = round((f30_arr[i] - 1.0) * 100.0, 1)

    df['spec_bucket_id'] = spec_ids
    df['spec_bucket_label'] = spec_labels
    df['price_band'] = price_bands
    df['price_band_label'] = price_band_lbls
    df['lifecycle_stage'] = lifecycle_stages
    df['launch_age_days'] = launch_ages
    df['archetype2_active'] = arch2_active
    df['archetype2_adj_pct'] = net_adj_pct

    pooled_cnt = 0
    if apply_enh and arch2_positions:
        arch2_idx = np.array(arch2_positions, dtype=int)
        base_fc7 = df['forecast_7d'].astype(float).values.copy()
        base_fc15 = df['forecast_15d'].astype(float).values.copy()
        base_fc30 = df['forecast_30d'].astype(float).values.copy()

        # Sparse Color/Variant Model-Family pooling in SKU mode (excludes Week-1 launches!)
        if gran == 'sku' and bucket_mult_sums is not None:
            pool_w, p_a7, p_a15, p_a30 = _compute_sparse_family_pool_targets(
                df=df,
                arch2_idx=arch2_idx,
                fam_ids=fam_ids_all,
                bucket_mult_sums=bucket_mult_sums,
                S7=S7,
                S15=S15,
                S30=S30,
                ref_dt=ref_dt,
                fc_engine=fc_engine,
            )
            # Do not dilute partial Week-1 launch SKUs with older family colors
            pool_w = np.where(s_age_arr <= 7, 0.0, pool_w)
            use_pool = pool_w > 0.0
            pooled_cnt = int(use_pool.sum())
            if pooled_cnt > 0:
                base_fc7 = np.where(use_pool, pool_w * p_a7 + (1.0 - pool_w) * base_fc7, base_fc7)
                base_fc15 = np.where(use_pool, pool_w * p_a15 + (1.0 - pool_w) * base_fc15, base_fc15)
                base_fc30 = np.where(use_pool, pool_w * p_a30 + (1.0 - pool_w) * base_fc30, base_fc30)

        mask = arch2_active
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

    active_cnt = int(arch2_active.sum())
    avg_adj = round(float(df.loc[arch2_active, 'archetype2_adj_pct'].mean()), 1) if active_cnt > 0 else 0.0
    stage_counts: Dict[str, int] = {}
    for st in lifecycle_stages:
        if st:
            stage_counts[st] = stage_counts.get(st, 0) + 1
    distinct_fams = len({fam_ids_all[i] for i in arch2_positions if fam_ids_all[i]})

    diagnostics = {
        'enabled': bool(active_cnt > 0),
        'archetype': 'Archetype 2 · Variant-Split Flagship Tech (Mobiles & Tablets)',
        'products_covered': list(ARCHETYPE_2_PRODUCTS),
        'enhanced_entities': active_cnt,
        'distinct_model_families': distinct_fams,
        'sparse_skus_pooled': pooled_cnt,
        'avg_adj_pct': avg_adj,
        'lifecycle_counts': stage_counts,
        'history_start': (profiles or {}).get('history_start', ''),
    }
    return df, diagnostics


def compute_archetype2_daily_curve(
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    brand: Any = '',
    icat: Any = '',
    pband: Any = '',
    lifecycle_stage: str = '',
) -> Optional[np.ndarray]:
    """
    30-day relative trajectory curve (mean=1.0) combining Price-Band smooth seasonal shape
    and subtle lifecycle slope (Post-Launch settling / Declining EOL).
    """
    p = ' '.join(str(prod or '').upper().strip().split())
    if p not in ARCHETYPE_2_SET:
        return None
    mo_idx = lookup_archetype2_month_index(profiles, p, brand, icat, pband)
    if mo_idx:
        vals = np.array(
            [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)],
            dtype=float,
        )
        mean_v = float(np.mean(vals))
        rel = (0.65 + 0.35 * (vals / mean_v)) if mean_v > 0 else np.ones(30, dtype=float)
    else:
        rel = np.ones(30, dtype=float)

    # Gentle decay slope across the 30-day horizon for Post-Launch Settling or Declining/EOL
    if lifecycle_stage == 'Post-Launch (8–30d)':
        rel = rel * np.linspace(1.08, 0.92, 30)
    elif lifecycle_stage == 'Declining / EOL':
        rel = rel * np.linspace(1.06, 0.94, 30)

    m = float(np.mean(rel))
    return (rel / m) if m > 0 else None
