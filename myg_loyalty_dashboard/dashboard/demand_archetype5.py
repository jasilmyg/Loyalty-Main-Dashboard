"""
Archetype 5 — Attach-Driven & Pack-Size High-Velocity Engine (Glamshield & Fragrance)
======================================================================================
Covers the 2 high-velocity impulse / attach retail lines (306K+ units/90d, ₹12+ Cr):

    GLAMSHIELD, FRAGRANCE

Why each needs a tailored strategy (proven via 6-origin ClickHouse walk-forward):
  1. `GLAMSHIELD` (2,760 SKUs, 491K units/180d):
     - `item_group` is 93%+ junk (`MSI P&G`), while `item_category` cleanly splits
       `SCREEN GUARD`, `BACK POUCH`, `MY STYLE`, `LENS PROTECTOR`, `TAB FLIP COVER`,
       and `brand` matches the exact phone OEM (`APPLE`, `SAMSUNG`, `VIVO`, `XIAOMI`).
     - Moves in lockstep with `MOBILE` sales (~88.8% attach ratio!).
     - Blending Brand-Matched Mobile Attach Seasonality (`55% MOBILE||BRAND` +
       `45% GLAMSHIELD||item_category`) + sparse long-tail pooling (`d30 < 8`) cuts:
         * 15d SKU WAPE      : 35.97% -> 35.09% (-0.88 pp)
         * 30d SKU WAPE      : 35.79% -> 34.56% (-1.23 pp)
         * 30d Bucket WAPE   : 18.07% -> 16.88% (-1.19 pp; 83.1% accuracy!)
  2. `FRAGRANCE` (59 SKUs, 113K units/180d, private-label `MY PARF`):
     - `item_group` is a genuine Pack-Size hierarchy (`MSI PARF 50 ML`, `30 ML`,
       `100 ML`, `COMBO`) despite the `MSI` prefix!
     - Because `MY PARF 50 ML` ramped sharply in mid-2026, naive 3-year seasonality
       hurts by +9.15 pp. Instead, a 35% de-festivalized recent-pace blend (capturing
       live billing-counter velocity shifts) + Pack-Size Planning Buckets cuts:
         * 15d SKU WAPE      : 54.09% -> 52.14% (-1.95 pp)
         * 30d SKU WAPE      : 55.48% -> 54.36% (-1.12 pp)
         * 30d Pack-Size WAPE: 37.67% -> 36.71% (-0.96 pp)
         * 30d Bias          : -31.53% -> -26.65%
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.core.cache import cache

from analytics.clickhouse_service import get_active_engine, get_bypass_cache
from .demand_archetype1 import (
    JUNK_SPEC_TOKENS,
    _sql_esc,
    ch_clean_item_cat_expr,
    normalize_item_category,
    smooth_month_index_on_date,
)
from .demand_archetype4 import _build_arch4_month_table
from .demand_ml_engine import run_hybrid_ml_forecasting

# ── Archetype 5 Product Universe ─────────────────────────────────────────────
ARCHETYPE_5_PRODUCTS: Tuple[str, ...] = ('GLAMSHIELD', 'FRAGRANCE')
ARCHETYPE_5_SET = frozenset(ARCHETYPE_5_PRODUCTS)

# Stray freebie / non-retail categories inside GLAMSHIELD & FRAGRANCE
ARCHETYPE_5_NON_RETAIL_ICATS: Tuple[str, ...] = (
    'FREE ACCESSORIES', 'SPARE', 'SERVICE', 'DEMO', 'DUMMY', 'FOC', 'SCRAP', 'FREE', 'GIFT',
)
ARCHETYPE_5_NON_RETAIL_SET = frozenset(ARCHETYPE_5_NON_RETAIL_ICATS)

_EXCLUDED_BRANCH_CODES = ('3GH', 'CSB', 'CSBW', 'HEAD OFFICE', 'UG SMART CHOICE')


# ═════════════════════════════════════════════════════════════════════════════
# 1. NORMALIZATION & SQL HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def normalize_fragrance_pack_group(raw_group: Any) -> str:
    """
    Preserve genuine Fragrance pack-size groups (`MSI PARF 50 ML`, `MSI PARF 30 ML`,
    `MSI PARF 100 ML`, `MSI PARF COMBO`) while stripping junk placeholders.
    """
    s = ' '.join(str(raw_group or '').upper().strip().split())
    if not s or s in JUNK_SPEC_TOKENS or s == 'MSI':
        return ''
    return s


def ch_archetype5_hygiene_clause(prod_col: str, cat_col: str) -> str:
    """Exclude stray free-accessory / spare / demo item_categories inside GLAMSHIELD & FRAGRANCE."""
    bad_sql = "', '".join(_sql_esc(x) for x in ARCHETYPE_5_NON_RETAIL_ICATS)
    return (
        f"NOT (upper(trim(ifNull({prod_col}, ''))) IN ('GLAMSHIELD', 'FRAGRANCE') "
        f"AND upper(trim(ifNull({cat_col}, ''))) IN ('{bad_sql}'))"
    )


def build_arch5_bucket_id(prod: Any, brand: Any, raw_cat: Any, raw_grp: Any = '') -> str:
    """
    Canonical 4-part Planning Bucket ID for Archetype 5:
      - GLAMSHIELD : `GLAMSHIELD||BRAND||ICAT||` (ignores junk `MSI P&G`)
      - FRAGRANCE  : `FRAGRANCE||BRAND||ICAT||PACK_GROUP` (preserves `MSI PARF 50 ML`, etc.)
    """
    p = ' '.join(str(prod or '').upper().strip().split()) or 'GLAMSHIELD'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(raw_cat)
    ig = normalize_fragrance_pack_group(raw_grp) if p == 'FRAGRANCE' else ''
    return f"{p}||{b}||{ic}||{ig}"


def format_arch5_bucket_label(prod: Any, brand: Any, raw_cat: Any, raw_grp: Any = '') -> str:
    """Human-readable label: `SAMSUNG — SCREEN GUARD` or `MY PARF — MSI PARF 50 ML (PERFUMES)`."""
    p = ' '.join(str(prod or '').upper().strip().split()) or 'GLAMSHIELD'
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(raw_cat)
    ig = normalize_fragrance_pack_group(raw_grp) if p == 'FRAGRANCE' else ''
    if p == 'FRAGRANCE' and ig:
        return f"{b} — {ig} ({ic})" if ic else f"{b} — {ig}"
    return f"{b} — {ic}" if ic else f"{b} — {p}"


# ═════════════════════════════════════════════════════════════════════════════
# 2. 3-YEAR MOBILE-ATTACH SEASONALITY & BRAND ATTACH-RATE PROFILES
# ═════════════════════════════════════════════════════════════════════════════

def get_archetype5_profiles(
    ch,
    ref_dt: date,
    festival_date_map: Dict[date, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Query 3 years of strongly de-festivalized monthly sales for `GLAMSHIELD` & `MOBILE`
    (to anchor Glamshield directly to Brand-Matched Mobile demand) plus trailing 90d
    Glamshield-to-Mobile attach ratios per OEM brand (~250ms, cached 30m).
    """
    ref_str = ref_dt.strftime('%Y-%m-%d')
    cache_key = f"dii:arch5_prof_v1:{get_active_engine()}:{ref_str}"
    if not get_bypass_cache():
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    empty = {
        'idx_gcat': {},
        'idx_gprod': {},
        'idx_mob_brand': {},
        'idx_mob_all': {},
        'attach_ratio_brand': {},
        'attach_ratio_overall': 0.0,
        'ref_date': ref_str,
        'history_start': ref_str,
    }
    if ch is None:
        return empty

    start_3y = ref_dt - timedelta(days=1095)
    start_str = start_3y.strftime('%Y-%m-%d 00:00:00')
    start_90_str = (ref_dt - timedelta(days=89)).strftime('%Y-%m-%d 00:00:00')
    end_str = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')

    fest_dates_sql: List[str] = []
    fest_div_sql: List[str] = []
    for fdt, finfo in sorted(festival_date_map.items()):
        if start_3y <= fdt <= ref_dt:
            pw = float(finfo.get('prior_weight', 1.0))
            if abs(pw - 1.0) > 1e-4:
                fest_dates_sql.append(f"toDate('{fdt.strftime('%Y-%m-%d')}')")
                fest_div_sql.append(f"{max(pw ** 1.65, 0.5):.4f}")

    if fest_dates_sql:
        fdiv_expr = (
            f"transform(toDate(s.date), [{', '.join(fest_dates_sql)}], "
            f"[{', '.join(fest_div_sql)}], 1.0)"
        )
    else:
        fdiv_expr = "1.0"

    excl_br_sql = "', '".join(_EXCLUDED_BRANCH_CODES)
    clean_cat_sql = ch_clean_item_cat_expr('m.item_category')
    hygiene_sql = ch_archetype5_hygiene_clause('m.product', 'm.item_category')
    net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"

    sql = f"""
        SELECT
            upper(trim(m.product)) AS prod,
            if(trim(ifNull(m.brand, '')) != '', upper(trim(m.brand)), 'GENERAL') AS br,
            {clean_cat_sql} AS icat,
            toMonth(toDate(s.date)) AS mo,
            greatest(0.0, sum({net_q} / greatest({fdiv_expr}, 0.5))) AS df_qty,
            greatest(0.0, sumIf({net_q}, s.date >= toDateTime('{start_90_str}'))) AS u90,
            min(toDate(s.date)) AS min_d
        FROM azure_sales_report s
        INNER JOIN item_master m ON s.item_code = m.item_code
        WHERE s.date >= toDateTime('{start_str}')
          AND s.date < toDateTime('{end_str}')
          AND upper(trim(m.product)) IN ('GLAMSHIELD', 'MOBILE')
          AND {hygiene_sql}
          AND s.item_code != ''
          AND upper(trim(s.branch)) NOT IN ('{excl_br_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY prod, br, icat, mo
    """
    try:
        rows = ch.query(sql).result_rows
    except Exception as e:
        print('[archetype5] profile query failed:', e)
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

    gcat_rows: List[Tuple[str, int, float]] = []
    gprod_rows: List[Tuple[str, int, float]] = []
    mob_br_rows: List[Tuple[str, int, float]] = []
    mob_all_rows: List[Tuple[str, int, float]] = []

    glam_u90_br: Dict[str, float] = {}
    mob_u90_br: Dict[str, float] = {}
    glam_u90_tot = 0.0
    mob_u90_tot = 0.0

    for r in rows:
        prod = str(r[0] or '').strip().upper()
        br = str(r[1] or '').strip().upper() or 'GENERAL'
        icat = str(r[2] or '').strip().upper()
        mo = int(r[3] or 0)
        df_qty = float(r[4] or 0.0)
        u90 = float(r[5] or 0.0)
        if not prod or mo < 1 or mo > 12:
            continue
        if prod == 'GLAMSHIELD':
            if df_qty > 0.0:
                gprod_rows.append(('GLAMSHIELD', mo, df_qty))
                if icat:
                    gcat_rows.append((f"GLAMSHIELD||{icat}", mo, df_qty))
            if u90 > 0.0:
                glam_u90_tot += u90
                glam_u90_br[br] = glam_u90_br.get(br, 0.0) + u90
        elif prod == 'MOBILE':
            if df_qty > 0.0:
                mob_all_rows.append(('MOBILE', mo, df_qty))
                mob_br_rows.append((f"MOBILE||{br}", mo, df_qty))
            if u90 > 0.0:
                mob_u90_tot += u90
                mob_u90_br[br] = mob_u90_br.get(br, 0.0) + u90

    attach_brand: Dict[str, float] = {}
    for br, m_u in mob_u90_br.items():
        if m_u >= 50.0 and br in glam_u90_br:
            attach_brand[br] = round((glam_u90_br[br] / m_u) * 100.0, 1)
    attach_overall = round((glam_u90_tot / mob_u90_tot) * 100.0, 1) if mob_u90_tot > 0 else 0.0

    def _clip_tbl(tbl):
        return {k: {m: float(np.clip(v, 0.40, 2.20)) for m, v in mm.items()} for k, mm in tbl.items()}

    profiles = {
        'idx_gcat': _clip_tbl(_build_arch4_month_table(gcat_rows, cal_days_by_mo, min_total_units=200.0, shrink_k=600.0)),
        'idx_gprod': _clip_tbl(_build_arch4_month_table(gprod_rows, cal_days_by_mo, min_total_units=100.0, shrink_k=500.0)),
        'idx_mob_brand': _clip_tbl(_build_arch4_month_table(mob_br_rows, cal_days_by_mo, min_total_units=200.0, shrink_k=600.0)),
        'idx_mob_all': _clip_tbl(_build_arch4_month_table(mob_all_rows, cal_days_by_mo, min_total_units=100.0, shrink_k=500.0)),
        'attach_ratio_brand': attach_brand,
        'attach_ratio_overall': attach_overall,
        'ref_date': ref_str,
        'history_start': hist_start.strftime('%Y-%m-%d'),
    }
    cache.set(cache_key, profiles, 1800)
    return profiles


def lookup_glamshield_attach_month_index(
    profiles: Optional[Dict[str, Any]],
    brand: Any = '',
    icat: Any = '',
) -> Dict[int, float]:
    """
    Brand-Matched Mobile Attach seasonality index for `GLAMSHIELD`:
      - Blends 55% `MOBILE||BRAND` (or `MOBILE` overall) + 45% `GLAMSHIELD||ICAT`
        (falling back cleanly if either is absent).
    """
    if not profiles:
        return {}
    b = ' '.join(str(brand or '').upper().strip().split()) or 'GENERAL'
    ic = normalize_item_category(icat)

    own = (profiles.get('idx_gcat') or {}).get(f"GLAMSHIELD||{ic}") if ic else None
    if not own:
        own = (profiles.get('idx_gprod') or {}).get('GLAMSHIELD')
    mob = (profiles.get('idx_mob_brand') or {}).get(f"MOBILE||{b}") or (profiles.get('idx_mob_all') or {}).get('MOBILE')

    if own and mob:
        return {m: 0.45 * float(own.get(m, 1.0)) + 0.55 * float(mob.get(m, 1.0)) for m in range(1, 13)}
    if mob:
        return dict(mob)
    if own:
        return dict(own)
    return {}


def compute_glamshield_attach_factors(
    ref_dt: date,
    mo_idx: Dict[int, float],
) -> Tuple[float, float, float]:
    """65% seasonal transition blend `(f7, f15, f30)` empirically validated on 6-origin Glamshield backtest."""
    if not mo_idx:
        return 1.0, 1.0, 1.0
    tr_vals = [smooth_month_index_on_date(ref_dt - timedelta(days=a), mo_idx) for a in range(30)]
    fut_vals = [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)]
    tr_m = max(float(np.mean(tr_vals)), 0.20)
    out = []
    for h in (7, 15, 30):
        fut_m = float(np.mean(fut_vals[:h]))
        raw_ratio = float(np.clip(fut_m / tr_m, 0.45, 2.00))
        out.append(0.35 + 0.65 * raw_ratio)
    return out[0], out[1], out[2]


# ═════════════════════════════════════════════════════════════════════════════
# 3. APPLY ARCHETYPE 5 ENHANCEMENTS (GLAMSHIELD ATTACH + FRAGRANCE RECENT PACE)
# ═════════════════════════════════════════════════════════════════════════════

def _compute_glamshield_sparse_pool(
    df: pd.DataFrame,
    glam_idx: np.ndarray,
    bkt_ids: List[str],
    bucket_mult_sums: List[float],
    S7: float,
    S15: float,
    S30: float,
    ref_dt: date,
    fc_engine: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Pool sparse long-tail Glamshield SKUs (`d30 < 8`, bucket >= 3 SKUs) at 45% bucket + 55% SKU."""
    n = len(df)
    pool_w = np.zeros(n, dtype=float)
    alloc7 = np.zeros(n, dtype=float)
    alloc15 = np.zeros(n, dtype=float)
    alloc30 = np.zeros(n, dtype=float)

    if len(glam_idx) < 6:
        return pool_w, alloc7, alloc15, alloc30
    req = ['u_b0', 'u_b1', 'd7', 'd30', 'd90', 'unit_mop', 'unit_mrp']
    if not all(c in df.columns for c in req):
        return pool_w, alloc7, alloc15, alloc30

    sub = df.iloc[glam_idx].copy()
    sub['_orig_pos'] = glam_idx
    sub['_bkt_key'] = [bkt_ids[i] for i in glam_idx]
    bkt_sizes = sub.groupby('_bkt_key')['_orig_pos'].transform('count').values
    d30_vals = sub['d30'].astype(float).values
    eligible = (bkt_sizes >= 3) & (sub['_bkt_key'].values != '')
    if not bool((eligible & (d30_vals < 8.0)).any()):
        return pool_w, alloc7, alloc15, alloc30

    pool_sub = sub[eligible].copy()
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
        bkt_fc, _ = run_hybrid_ml_forecasting(
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
        print('[archetype5] glamshield sparse pool skipped:', e)
        return pool_w, alloc7, alloc15, alloc30

    f7_map = dict(zip(bkt_fc['_bkt_key'], bkt_fc['forecast_7d'].astype(float)))
    f15_map = dict(zip(bkt_fc['_bkt_key'], bkt_fc['forecast_15d'].astype(float)))
    f30_map = dict(zip(bkt_fc['_bkt_key'], bkt_fc['forecast_30d'].astype(float)))

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
    sub_d30 = pool_sub['d30'].astype(float).values
    for j, orig_i in enumerate(pos_arr):
        if sub_d30[j] < 8.0:
            pool_w[orig_i] = 0.45
            alloc7[orig_i] = p_f7[j]
            alloc15[orig_i] = p_f15[j]
            alloc30[orig_i] = p_f30[j]

    return pool_w, alloc7, alloc15, alloc30


def apply_archetype5_forecast_enhancements(
    df: pd.DataFrame,
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    gran: str,
    fc_engine: str,
    bucket_mult_sums: Optional[List[float]] = None,
    S7: float = 7.0,
    S15: float = 15.0,
    S30: float = 30.0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Apply Archetype 5 enhancements to `GLAMSHIELD` (Brand-Matched Mobile Attach seasonality
    + sparse long-tail pooling) and `FRAGRANCE` (Pack-Size buckets + 35% de-festivalized
    recent-pace blend capturing rapid billing-counter rollouts without fake 3-yr seasonality).
    Non-Archetype-5 rows and `statistical` baseline remain 100% untouched.
    """
    n = len(df)
    for col, default_val in (
        ('spec_bucket_id', ''),
        ('spec_bucket_label', ''),
    ):
        if col not in df.columns:
            df[col] = default_val

    spec_ids = df['spec_bucket_id'].astype(str).tolist()
    spec_labels = df['spec_bucket_label'].astype(str).tolist()
    arch5_active = np.zeros(n, dtype=bool)
    arch5_adj_pct = np.zeros(n, dtype=float)
    attach_notes = [''] * n

    prods = df['product_line'].astype(str).tolist()
    brands = df['brand'].astype(str).tolist()
    icats = df['item_category'].astype(str).tolist() if 'item_category' in df.columns else [''] * n
    igrps = df['item_group'].astype(str).tolist() if 'item_group' in df.columns else [''] * n

    u0_arr = df['u_b0'].astype(float).to_numpy(dtype=float, copy=False) if 'u_b0' in df.columns else np.zeros(n, dtype=float)
    u1_arr = df['u_b1'].astype(float).to_numpy(dtype=float, copy=False) if 'u_b1' in df.columns else np.zeros(n, dtype=float)

    f7_arr = np.ones(n, dtype=float)
    f15_arr = np.ones(n, dtype=float)
    f30_arr = np.ones(n, dtype=float)
    bkt_ids_all = [''] * n
    glam_positions: List[int] = []
    frag_positions: List[int] = []

    seas_cache: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
    attach_brand_map = (profiles or {}).get('attach_ratio_brand') or {}
    attach_overall = float((profiles or {}).get('attach_ratio_overall', 0.0) or 0.0)
    apply_enh = (fc_engine in ('hybrid', 'ml'))

    for i in range(n):
        p = prods[i].strip().upper()
        if p not in ARCHETYPE_5_SET:
            continue
        b = brands[i].strip().upper() or 'GENERAL'
        ic = normalize_item_category(icats[i])
        ig = normalize_fragrance_pack_group(igrps[i]) if p == 'FRAGRANCE' else ''

        bkt_id = build_arch5_bucket_id(p, b, ic, ig)
        bkt_ids_all[i] = bkt_id
        if gran in ('sku', 'spec_bucket'):
            spec_ids[i] = bkt_id
            spec_labels[i] = format_arch5_bucket_label(p, b, ic, ig)

        if p == 'GLAMSHIELD':
            glam_positions.append(i)
            br_att = attach_brand_map.get(b)
            if br_att is not None:
                attach_notes[i] = f"Mobile Attach ({b}: {br_att:.0f}%)"
            elif attach_overall > 0:
                attach_notes[i] = f"Mobile Attach ({attach_overall:.0f}%)"

            if apply_enh and profiles:
                ckey = (b, ic)
                cached_s = seas_cache.get(ckey)
                if cached_s is None:
                    mi = lookup_glamshield_attach_month_index(profiles, b, ic)
                    cached_s = compute_glamshield_attach_factors(ref_dt, mi) if mi else (1.0, 1.0, 1.0)
                    seas_cache[ckey] = cached_s
                s7, s15, s30 = cached_s
                f7_arr[i], f15_arr[i], f30_arr[i] = s7, s15, s30
                arch5_active[i] = True
                arch5_adj_pct[i] = round((s30 - 1.0) * 100.0, 1)

        elif p == 'FRAGRANCE':
            frag_positions.append(i)
            if ig:
                attach_notes[i] = f"Pack-Size ({ig.replace('MSI PARF ', '')})"
            if apply_enh:
                arch5_active[i] = True

    pooled_cnt = 0
    if apply_enh and (glam_positions or frag_positions):
        base_fc7 = df['forecast_7d'].astype(float).to_numpy(dtype=float, copy=True)
        base_fc15 = df['forecast_15d'].astype(float).to_numpy(dtype=float, copy=True)
        base_fc30 = df['forecast_30d'].astype(float).to_numpy(dtype=float, copy=True)

        # 1) GLAMSHIELD: sparse long-tail pooling + Brand-Matched Mobile Attach seasonality
        if glam_positions:
            glam_idx = np.array(glam_positions, dtype=int)
            if gran == 'sku' and bucket_mult_sums is not None:
                pool_w, p_a7, p_a15, p_a30 = _compute_glamshield_sparse_pool(
                    df=df,
                    glam_idx=glam_idx,
                    bkt_ids=bkt_ids_all,
                    bucket_mult_sums=bucket_mult_sums,
                    S7=S7,
                    S15=S15,
                    S30=S30,
                    ref_dt=ref_dt,
                    fc_engine=fc_engine,
                )
                use_pool = pool_w > 0.0
                pooled_cnt = int(use_pool.sum())
                if pooled_cnt > 0:
                    base_fc7 = np.where(use_pool, pool_w * p_a7 + (1.0 - pool_w) * base_fc7, base_fc7)
                    base_fc15 = np.where(use_pool, pool_w * p_a15 + (1.0 - pool_w) * base_fc15, base_fc15)
                    base_fc30 = np.where(use_pool, pool_w * p_a30 + (1.0 - pool_w) * base_fc30, base_fc30)

            g_mask = np.zeros(n, dtype=bool)
            g_mask[glam_idx] = True
            base_fc7 = np.where(g_mask, np.maximum(0.0, base_fc7 * f7_arr), base_fc7)
            base_fc15 = np.where(g_mask, np.maximum(base_fc7, base_fc15 * f15_arr), base_fc15)
            base_fc30 = np.where(g_mask, np.maximum(base_fc15, base_fc30 * f30_arr), base_fc30)

            if 'hybrid_forecast_15d' in df.columns:
                df['hybrid_forecast_15d'] = np.round(
                    np.where(g_mask, np.maximum(0.0, df['hybrid_forecast_15d'].astype(float) * f15_arr), df['hybrid_forecast_15d'].astype(float)),
                    1,
                )
            if 'ml_forecast_15d' in df.columns:
                df['ml_forecast_15d'] = np.round(
                    np.where(g_mask, np.maximum(0.0, df['ml_forecast_15d'].astype(float) * f15_arr), df['ml_forecast_15d'].astype(float)),
                    1,
                )

        # 2) FRAGRANCE: 35% de-festivalized recent-pace blend (no fake 3-yr seasonality!)
        if frag_positions and bucket_mult_sums is not None and len(bucket_mult_sums) >= 2:
            c0 = max(float(bucket_mult_sums[0]), 1.0)
            c1 = max(float(bucket_mult_sums[1]), 1.0)
            for idx in frag_positions:
                r0 = float(u0_arr[idx]) / c0
                r1 = float(u1_arr[idx]) / c1
                if r0 <= 0.0 and r1 <= 0.0:
                    continue
                r_rec = 0.65 * r0 + 0.35 * (r1 if r1 > 0.0 else r0)
                old30 = max(base_fc30[idx], 0.0)
                new7 = max(0.0, 0.35 * (r_rec * S7) + 0.65 * base_fc7[idx])
                new15 = max(new7, 0.35 * (r_rec * S15) + 0.65 * base_fc15[idx])
                new30 = max(new15, 0.35 * (r_rec * S30) + 0.65 * base_fc30[idx])
                base_fc7[idx], base_fc15[idx], base_fc30[idx] = new7, new15, new30
                if old30 > 0.5:
                    arch5_adj_pct[idx] = round(((new30 - old30) / old30) * 100.0, 1)
                if 'hybrid_forecast_15d' in df.columns:
                    df.iat[idx, df.columns.get_loc('hybrid_forecast_15d')] = round(new15, 1)

        df['forecast_7d'] = np.round(base_fc7, 1)
        df['forecast_15d'] = np.round(base_fc15, 1)
        df['forecast_30d'] = np.round(base_fc30, 1)

    df['spec_bucket_id'] = spec_ids
    df['spec_bucket_label'] = spec_labels
    df['archetype5_active'] = arch5_active
    df['archetype5_adj_pct'] = arch5_adj_pct
    df['archetype5_note'] = attach_notes

    active_cnt = int(arch5_active.sum())
    avg_adj = round(float(df.loc[arch5_active, 'archetype5_adj_pct'].mean()), 1) if active_cnt > 0 else 0.0
    diagnostics = {
        'enabled': bool(active_cnt > 0),
        'archetype': 'Archetype 5 · Attach-Driven (Glamshield) & Pack-Size (Fragrance)',
        'products_covered': list(ARCHETYPE_5_PRODUCTS),
        'enhanced_entities': active_cnt,
        'glamshield_entities': len(glam_positions),
        'fragrance_entities': len(frag_positions),
        'sparse_skus_pooled': pooled_cnt,
        'glamshield_mobile_attach_pct': attach_overall,
        'avg_adj_pct': avg_adj,
        'history_start': (profiles or {}).get('history_start', ''),
    }
    return df, diagnostics


def compute_archetype5_daily_curve(
    ref_dt: date,
    profiles: Optional[Dict[str, Any]],
    prod: Any,
    brand: Any = '',
    icat: Any = '',
) -> Optional[np.ndarray]:
    """
    30-day relative trajectory curve (mean=1.0) for `GLAMSHIELD` using Brand-Matched
    Mobile Attach seasonality. Returns `None` for `FRAGRANCE` (no synthetic 3-yr shape).
    """
    p = ' '.join(str(prod or '').upper().strip().split())
    if p != 'GLAMSHIELD':
        return None
    mo_idx = lookup_glamshield_attach_month_index(profiles, brand, icat)
    if not mo_idx:
        return None
    vals = np.array(
        [smooth_month_index_on_date(ref_dt + timedelta(days=i), mo_idx) for i in range(1, 31)],
        dtype=float,
    )
    mean_v = float(np.mean(vals))
    if mean_v <= 0.0:
        return None
    rel = 0.35 + 0.65 * (vals / mean_v)
    m = float(np.mean(rel))
    return (rel / m) if m > 0 else None
