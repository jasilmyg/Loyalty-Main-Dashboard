"""
Inter-Store Stock Transfer Intelligence Engine (100% ClickHouse-Backed)
=======================================================================
Automatically identifies:
  1. Stores with excess stock (strictly AFTER reserving each store's AI forecast demand + safety stock).
  2. Stores with high demand and insufficient stock (accounting for on-hand + goods-in-transit).
  3. Products (SKUs) suitable for inter-store stock transfers.
  4. Recommended transfer quantities (mass-balanced: Sum(Transfer) == Sum(Receive), prioritizing intra-district routes).
  5. Expected reduction in ageing stock (60+ days & 90+ days aged inventory cleared from slow/surplus stores).
  6. Potential sales opportunity (incremental units & ₹ revenue rescued from imminent store stockouts).

ClickHouse Source Tables:
  - stock_ageing_report (store-level on-hand stock, transit, total_age buckets, mop, cost_price)
  - azure_sales_report  (store-level 15d, 30d, 60d historical unit & revenue velocity)
  - branch_master       (retail showroom code <-> branch_name, district, store_type)
  - item_master         (product line, brand, retail classification)
"""

from __future__ import annotations

import math
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd


# Keywords identifying non-showroom / central / damaged locations in `branch_master` or `stock_ageing_report`
NON_SHOWROOM_KEYWORDS = (
    'CENTRAL',
    'WAREHOUSE',
    'HEAD OFFICE',
    'CORPORATE',
    'DAMAGE',
    'SCRAP',
    'SERVICE',
    'DUMMY',
    'B2B',
    'ONLINE',
    'ECOM',
    'ADMIN',
    'PROJECT',
    'FRANCHISEE HO',
)


def _is_retail_showroom_name(bname: str) -> bool:
    if not bname:
        return False
    up = bname.strip().upper()
    if up in ('HO', 'CSB', 'CSBW', '3GH'):
        return False
    for kw in NON_SHOWROOM_KEYWORDS:
        if kw in up:
            return False
    return True


def _build_retail_store_maps(
    bm_df: pd.DataFrame,
    excluded_codes: Tuple[str, ...],
    district_filter: str = '',
) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """
    Returns:
      - store_meta[code] = {'code': code, 'name': branch_name, 'district': district}
      - stock_bname_to_code[upper_stock_branch_name] = code
        (maps both 'SHOWROOM NAME' and 'SHOWROOM NAME GODOWN' to that showroom's code,
         while ignoring central warehouses / non-showroom locations).
    """
    store_meta: Dict[str, Dict[str, str]] = {}
    stock_bname_to_code: Dict[str, str] = {}
    dist_clean = (district_filter or '').strip().upper()

    if bm_df is None or bm_df.empty:
        return store_meta, stock_bname_to_code

    for _, r in bm_df.iterrows():
        code = str(r.get('code', '')).strip()
        bname = str(r.get('branch_name', '')).strip().upper()
        dist = str(r.get('district', '')).strip().upper() or 'OTHER'
        stype = str(r.get('store_type', '')).strip().upper()

        if not code or not bname:
            continue
        if code in excluded_codes:
            continue
        if not _is_retail_showroom_name(bname):
            continue
        if stype and any(k in stype for k in ('WAREHOUSE', 'GODOWN', 'OFFICE', 'SERVICE')):
            continue
        if dist_clean and dist != dist_clean:
            continue

        store_meta[code] = {
            'code': code,
            'name': bname,
            'district': dist,
        }
        stock_bname_to_code[bname] = code
        stock_bname_to_code[f"{bname} GODOWN"] = code

    return store_meta, stock_bname_to_code


def _compute_store_safety_stock(fc_15d: np.ndarray, active_days_30: np.ndarray) -> np.ndarray:
    """
    95% Cycle Service Level (z = 1.65) safety stock over a 5-day inter-store transfer & review lead time:
      - For stores with active forecasted demand (fc_15d >= 0.45):
          sigma_daily = sqrt(max(daily_fc, 0.05) * (1 + 0.40 * I[intermittent]))
          SS = max(1, round(1.65 * sigma_daily * sqrt(5) * 0.45 + 0.20 * fc_15d))
      - For stores with zero forecasted demand (fc_15d < 0.45):
          SS = 0 (display buffer for fresh <60d zero-sale stock is handled separately).
    """
    daily_fc = fc_15d / 15.0
    intermittent_bump = np.where(active_days_30 < 6.0, 1.40, 1.0)
    sigma_d = np.sqrt(np.maximum(daily_fc, 0.05) * intermittent_bump)
    raw_ss = np.round(1.65 * sigma_d * math.sqrt(5.0) * 0.45 + 0.20 * fc_15d)
    return np.where(fc_15d >= 0.45, np.maximum(1.0, raw_ss), 0.0)


def compute_inter_store_transfer_intelligence(
    ch,
    ref_dt: date,
    stock_dt: Optional[date],
    bm_df: pd.DataFrame,
    stock_table: str,
    excluded_branch_codes: Tuple[str, ...],
    non_retail_products: Tuple[str, ...],
    virtual_vas_products: Tuple[str, ...],
    should_exclude_vas: bool,
    macro_prods: Optional[List[str]],
    product_filter: str = '',
    brand_filter: str = '',
    district_filter: str = '',
    branch_filter: str = '',
    S15: float = 15.0,
    S30: float = 30.0,
    hist_mult_sums: Optional[Dict[str, float]] = None,
    sku_uplift_map: Optional[Dict[str, float]] = None,
    selected_sku_hint: str = '',
    max_products: int = 50,
) -> Dict[str, Any]:
    """
    Computes end-to-end Inter-Store Stock Transfer Intelligence 100% from ClickHouse.

    Guarantees:
      - No donor store is ever recommended to transfer units unless its current on-hand stock
        strictly exceeds ceil(Store_15d_Forecast + Store_Safety_Stock) AND satisfies healthy
        coverage (>=28 days cover or aged 60+d surplus).
      - Receiver stores only receive up to ceil(Store_15d_Forecast + Store_Safety_Stock) - ceil(OnHand + Transit).
      - Strictly Same-District transfers ONLY (from_district == to_district != 'OTHER'), eliminating
        costly cross-district freight across all UI views and Excel exports.
      - Exact mass conservation: Sum(Transfer Out) == Sum(Receive In) for every product and district.
    """
    t0 = time.perf_counter()

    empty_payload = {
        'kpis': {
            'excess_stores_count': 0,
            'excess_units_total': 0,
            'deficit_stores_count': 0,
            'deficit_units_total': 0,
            'transferable_products_count': 0,
            'recommended_transfer_qty': 0,
            'intra_district_share_pct': 0.0,
            'expected_ageing_reduction_units': 0,
            'expected_ageing_reduction_90d_units': 0,
            'expected_ageing_reduction_val': 0.0,
            'potential_sales_opportunity_units': 0,
            'potential_sales_opportunity_val': 0.0,
            'compute_ms': 0.0,
        },
        'selected_transfer_product': None,
        'transfer_products': [],
        'transfer_routes': [],
        'excess_stores': [],
        'deficit_stores': [],
    }

    if ch is None or stock_dt is None:
        return empty_payload

    store_meta, stock_bname_to_code = _build_retail_store_maps(
        bm_df=bm_df,
        excluded_codes=excluded_branch_codes,
        district_filter=district_filter,
    )
    if len(store_meta) < 2:
        return empty_payload

    def _esc(v: str) -> str:
        return str(v).replace("\\", "\\\\").replace("'", "''")

    # Resolve optional single-branch focus filter (so we still match across the network,
    # then filter recommendations involving the selected branch as Donor or Receiver!)
    focus_store_code = ''
    br_clean = (branch_filter or '').strip().upper()
    if br_clean:
        if br_clean in store_meta:
            focus_store_code = br_clean
        elif br_clean in stock_bname_to_code:
            focus_store_code = stock_bname_to_code[br_clean]

    # ── 1. Query Store × SKU Inventory & Ageing Snapshot from ClickHouse `stock_ageing_report` ──
    st_clauses = [
        f"report_date = toDate('{stock_dt.strftime('%Y-%m-%d')}')",
        "item_code != ''",
        "product != ''",
    ]
    excl_prods = list(non_retail_products)
    if should_exclude_vas:
        excl_prods.extend(virtual_vas_products)
        st_clauses.append("NOT (upper(item) LIKE '%:SERVICE%' OR upper(item) LIKE '%EXTENDED WARRANTY%')")
    if excl_prods:
        ep_sql = "', '".join(_esc(p) for p in excl_prods)
        st_clauses.append(f"upper(product) NOT IN ('{ep_sql}')")
    if macro_prods:
        mp_sql = "', '".join(_esc(p) for p in macro_prods)
        st_clauses.append(f"upper(product) IN ('{mp_sql}')")
    if product_filter:
        st_clauses.append(f"upper(product) = '{_esc(product_filter.strip().upper())}'")
    if brand_filter:
        st_clauses.append(f"upper(brand) = '{_esc(brand_filter.strip().upper())}'")

    valid_bnames = sorted(stock_bname_to_code.keys())
    bnames_sql = "', '".join(_esc(b) for b in valid_bnames)
    st_clauses.append(f"upper(trim(branch)) IN ('{bnames_sql}')")
    st_where_sql = " AND ".join(st_clauses)

    stock_sql = f"""
        SELECT
            item_code,
            upper(trim(branch)) AS bname,
            any(if(item != '', item, item_code)) AS item_name,
            upper(any(product)) AS prod_line,
            upper(any(if(brand != '', brand, 'GENERAL'))) AS brand_name,
            sum(greatest(0.0, toFloat64(stock))) AS on_hand,
            sum(greatest(0.0, toFloat64(transit))) AS in_transit,
            sumIf(greatest(0.0, toFloat64(stock)), total_age >= 60) AS aged_60_qty,
            sumIf(greatest(0.0, toFloat64(stock)), total_age > 90) AS aged_90_qty,
            sum(greatest(0.0, toFloat64(stock)) * toFloat64(total_age)) AS age_weighted_sum,
            anyIf(toFloat64(mop), mop > 0 AND mop < 1000000) AS unit_mop,
            anyIf(toFloat64(cost_price), cost_price > 0) AS unit_cost
        FROM {stock_table}
        WHERE {st_where_sql}
        GROUP BY item_code, bname
        HAVING on_hand > 0 OR in_transit > 0
    """
    st_rows = ch.query(stock_sql).result_rows
    if not st_rows:
        return empty_payload

    df_st_raw = pd.DataFrame(
        st_rows,
        columns=[
            'item_code', 'bname', 'item_name', 'prod_line', 'brand_name',
            'on_hand', 'in_transit', 'aged_60_qty', 'aged_90_qty',
            'age_weighted_sum', 'unit_mop', 'unit_cost',
        ],
    )
    df_st_raw['store_code'] = df_st_raw['bname'].map(stock_bname_to_code)
    df_st_raw = df_st_raw[df_st_raw['store_code'].notna()].copy()
    if df_st_raw.empty:
        return empty_payload

    for c in ('on_hand', 'in_transit', 'aged_60_qty', 'aged_90_qty', 'age_weighted_sum', 'unit_mop', 'unit_cost'):
        df_st_raw[c] = pd.to_numeric(df_st_raw[c], errors='coerce').fillna(0.0)

    # Extract SKU string metadata once (avoids slow Python object `.first()` across 75k groups)
    sku_str_df = df_st_raw[['item_code', 'item_name', 'prod_line', 'brand_name']].drop_duplicates('item_code')

    # Collapse showroom + attached "SHOWROOM GODOWN" into canonical (item_code, store_code) using pure Cython float64 ops
    df_st = df_st_raw.groupby(['item_code', 'store_code'], as_index=False).agg(
        on_hand=('on_hand', 'sum'),
        in_transit=('in_transit', 'sum'),
        aged_60_qty=('aged_60_qty', 'sum'),
        aged_90_qty=('aged_90_qty', 'sum'),
        age_weighted_sum=('age_weighted_sum', 'sum'),
        unit_mop=('unit_mop', 'max'),
        unit_cost=('unit_cost', 'max'),
    )
    df_st['avg_age'] = np.where(
        df_st['on_hand'] > 0,
        df_st['age_weighted_sum'] / np.maximum(df_st['on_hand'], 1e-6),
        0.0,
    )

    # SKU Metadata Lookup (pure numeric groupby + fast join with string metadata)
    sku_num_df = df_st.groupby('item_code', as_index=False).agg(
        unit_mop=('unit_mop', 'max'),
        unit_cost=('unit_cost', 'max'),
        max_store_stock=('on_hand', 'max'),
        aged_60_total=('aged_60_qty', 'sum'),
        total_stock=('on_hand', 'sum'),
    )
    sku_meta_df = pd.merge(sku_num_df, sku_str_df, on='item_code', how='left')

    # Candidate Pruning: An SKU can ONLY have a donor store if at least one store holds >= 2 units
    # (or 1 unit that is aged >= 60 days with 0 local sales)!
    cand_mask = (sku_meta_df['max_store_stock'] >= 2.0) | (sku_meta_df['aged_60_total'] >= 1.0)
    cand_df = sku_meta_df[cand_mask].copy()
    if cand_df.empty:
        return empty_payload

    # Cap candidate SKUs to top 1,500 by stock value & aged stock so ClickHouse `IN (...)` stays tiny & fast (<150ms)
    if len(cand_df) > 1500:
        eff_p = np.where(cand_df['unit_mop'] > 0, cand_df['unit_mop'], np.maximum(cand_df['unit_cost'], 100.0))
        cand_df['_prio'] = (cand_df['total_stock'] + 1.5 * cand_df['aged_60_total']) * np.log1p(eff_p)
        cand_df = cand_df.sort_values('_prio', ascending=False).head(1500)

    cand_skus: Set[str] = set(cand_df['item_code'].astype(str).tolist())
    if selected_sku_hint and selected_sku_hint != '__ALL__' and '||' not in selected_sku_hint:
        if selected_sku_hint in set(sku_meta_df['item_code'].astype(str)):
            cand_skus.add(selected_sku_hint)

    df_st = df_st[df_st['item_code'].isin(cand_skus)].copy()
    sku_meta_sub = sku_meta_df[sku_meta_df['item_code'].isin(cand_skus)]
    sku_meta_map: Dict[str, Dict[str, Any]] = {}
    for row in sku_meta_sub.itertuples(index=False):
        ic = str(row.item_code)
        umop = float(row.unit_mop)
        ucost = float(row.unit_cost)
        up = umop if umop > 0 else (ucost * 1.12 if ucost > 0 else 0.0)
        sku_meta_map[ic] = {
            'code': ic,
            'name': str(row.item_name or ic),
            'product_line': str(row.prod_line or 'OTHER'),
            'brand': str(row.brand_name or 'GENERAL'),
            'unit_price': up,
            'unit_cost': ucost,
        }

    # ── 2. Query Store × SKU 60-Day Sales Velocity from ClickHouse `azure_sales_report` ──
    d_ref_str = ref_dt.strftime('%Y-%m-%d')
    cutoff_60_str = (ref_dt - timedelta(days=59)).strftime('%Y-%m-%d 00:00:00')
    end_dt_str = (ref_dt + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    valid_codes_sql = "', '".join(_esc(c) for c in sorted(store_meta.keys()))
    cand_skus_sql = "', '".join(_esc(s) for s in sorted(cand_skus))

    net_q = "if(s.sold_price < 0, -abs(toFloat64(s.qty)), abs(toFloat64(s.qty)))"
    age_expr = f"dateDiff('day', toDate(s.date), toDate('{d_ref_str}'))"

    sales_sql = f"""
        SELECT
            s.item_code AS item_code,
            s.branch AS store_code,
            greatest(0.0, sumIf({net_q}, {age_expr} >= 0 AND {age_expr} <= 14)) AS d15,
            greatest(0.0, sumIf({net_q}, {age_expr} >= 0 AND {age_expr} <= 29)) AS d30,
            greatest(0.0, sum({net_q})) AS d60,
            countDistinctIf(toDate(s.date), {age_expr} >= 0 AND {age_expr} <= 29 AND s.sold_price > 0) AS active_days_30,
            greatest(0.0, sum(toFloat64(s.sold_price))) AS rev60
        FROM azure_sales_report s
        WHERE s.date >= toDateTime('{cutoff_60_str}')
          AND s.date < toDateTime('{end_dt_str}')
          AND s.branch IN ('{valid_codes_sql}')
          AND s.item_code IN ('{cand_skus_sql}')
          AND NOT (upper(s.invoice_no) LIKE '%SMC%' OR upper(s.invoice_no) LIKE '%EI%')
        GROUP BY item_code, store_code
        HAVING d60 > 0
    """
    sl_rows = ch.query(sales_sql).result_rows
    if not sl_rows:
        return empty_payload

    df_sl = pd.DataFrame(
        sl_rows,
        columns=['item_code', 'store_code', 'd15', 'd30', 'd60', 'active_days_30', 'rev60'],
    )
    for c in ('d15', 'd30', 'd60', 'active_days_30', 'rev60'):
        df_sl[c] = pd.to_numeric(df_sl[c], errors='coerce').fillna(0.0)

    # Update SKU realized selling price where sales revenue is available
    sku_rev = df_sl.groupby('item_code', as_index=False).agg(d60=('d60', 'sum'), rev60=('rev60', 'sum'))
    for _, r in sku_rev.iterrows():
        ic = str(r['item_code'])
        if ic in sku_meta_map and float(r['d60']) > 0 and float(r['rev60']) > 0:
            realized_p = float(r['rev60']) / float(r['d60'])
            if realized_p > 10.0:
                # Blend realized ASP softly with MOP if MOP exists, else use realized ASP
                mop_p = sku_meta_map[ic]['unit_price']
                sku_meta_map[ic]['unit_price'] = realized_p if mop_p <= 0 else (0.70 * realized_p + 0.30 * mop_p)

    # ── 3. Outer Merge Store Stock + Store Sales & Compute Store Forecast + Safety Stock ──
    grid = pd.merge(
        df_st[['item_code', 'store_code', 'on_hand', 'in_transit', 'aged_60_qty', 'aged_90_qty', 'avg_age']],
        df_sl[['item_code', 'store_code', 'd15', 'd30', 'd60', 'active_days_30']],
        on=['item_code', 'store_code'],
        how='outer',
    )
    for c in ('on_hand', 'in_transit', 'aged_60_qty', 'aged_90_qty', 'avg_age', 'd15', 'd30', 'd60', 'active_days_30'):
        grid[c] = pd.to_numeric(grid[c], errors='coerce').fillna(0.0)

    hms = hist_mult_sums or {}
    H15 = max(float(hms.get('H15', 15.0)), 5.0)
    H30 = max(float(hms.get('H30', 30.0)), 10.0)
    H60 = max(H30 * 2.0, 20.0)

    # De-seasonalized store daily velocity
    v15 = grid['d15'].values / H15
    v30 = grid['d30'].values / H30
    v60 = grid['d60'].values / H60
    base_store_vel = 0.45 * v15 + 0.35 * v30 + 0.20 * v60

    # Apply Hybrid AI (LightGBM + Croston-TSB) SKU-level uplift multiplier where available
    if sku_uplift_map:
        uplifts = grid['item_code'].map(sku_uplift_map).fillna(1.0).values.astype(float)
        uplifts = np.clip(uplifts, 0.75, 1.45)
    else:
        uplifts = np.ones(len(grid), dtype=float)

    eff_daily_fc = base_store_vel * uplifts
    fc_15d = eff_daily_fc * float(S15)
    fc_30d = eff_daily_fc * float(S30)
    safety_stock = _compute_store_safety_stock(fc_15d, grid['active_days_30'].values)

    on_hand = np.floor(grid['on_hand'].values)
    in_transit = np.ceil(grid['in_transit'].values)
    eff_supply = on_hand + in_transit
    aged_60 = np.minimum(on_hand, np.round(grid['aged_60_qty'].values))
    aged_90 = np.minimum(on_hand, np.round(grid['aged_90_qty'].values))
    avg_age = grid['avg_age'].values

    # Display buffer: keep 1 unit at a 0-demand store ONLY if its stock is fresh (<60 days old);
    # if stock is aged >= 60 days with 0 sales in 60 days, protected_stock = 0 so dead stock can be cleared!
    fresh_zero_buffer = np.where((fc_15d < 0.45) & (aged_60 < 1.0) & (avg_age < 60.0), 1.0, 0.0)
    protected_stock = np.maximum(np.ceil(fc_15d + safety_stock), fresh_zero_buffer)

    coverage_days = np.where(
        eff_daily_fc > 0.01,
        on_hand / np.maximum(eff_daily_fc, 1e-6),
        np.where(on_hand > 0, 999.0, 0.0),
    )

    # Donor Surplus: strictly above Protected Stock (15d Forecast + Safety Stock) AND comfortable cover (>=28d or aged 60+d)
    is_eligible_donor = (on_hand > protected_stock) & ((coverage_days >= 28.0) | (aged_60 >= 1.0) | (grid['d60'].values <= 0.0))
    donor_excess = np.where(is_eligible_donor, np.maximum(0.0, on_hand - protected_stock), 0.0)

    # Receiver Deficit: active high demand (fc_15d >= 1.15 or d30 >= 2) AND insufficient supply (< Forecast + Safety Stock)
    required_stock = np.ceil(fc_15d + safety_stock)
    has_real_demand = (fc_15d >= 1.15) | ((grid['d30'].values >= 2.0) & (fc_15d >= 0.85))
    is_eligible_receiver = has_real_demand & (eff_supply < required_stock) & (coverage_days < 19.0)
    receiver_deficit = np.where(is_eligible_receiver, np.maximum(0.0, required_stock - eff_supply), 0.0)

    grid['daily_fc'] = np.round(eff_daily_fc, 3)
    grid['fc_15d'] = np.round(fc_15d, 1)
    grid['fc_30d'] = np.round(fc_30d, 1)
    grid['safety_stock'] = safety_stock.astype(int)
    grid['protected_stock'] = protected_stock.astype(int)
    grid['required_stock'] = required_stock.astype(int)
    grid['on_hand_int'] = on_hand.astype(int)
    grid['in_transit_int'] = in_transit.astype(int)
    grid['aged_60_int'] = aged_60.astype(int)
    grid['aged_90_int'] = aged_90.astype(int)
    grid['coverage_days'] = np.round(np.minimum(coverage_days, 999.0), 1)
    grid['donor_excess'] = donor_excess.astype(int)
    grid['receiver_deficit'] = receiver_deficit.astype(int)

    # Keep only rows that are either a Donor or a Receiver to speed up network matching!
    active_grid = grid[(grid['donor_excess'] > 0) | (grid['receiver_deficit'] > 0)].copy()
    if active_grid.empty:
        return empty_payload

    store_dist_lookup = {k: v['district'] for k, v in store_meta.items()}
    store_name_lookup = {k: v['name'] for k, v in store_meta.items()}
    active_grid['district'] = active_grid['store_code'].map(store_dist_lookup).fillna('OTHER')
    active_grid['store_name'] = active_grid['store_code'].map(store_name_lookup).fillna(active_grid['store_code'])

    # Still aggregate store-wide Excess & Deficit totals across active SKUs before pairing
    # so "Stores with Excess Stock" & "Stores with High Demand & Insufficient Stock" reflect full truth!
    excess_store_agg: Dict[str, Dict[str, Any]] = {}
    deficit_store_agg: Dict[str, Dict[str, Any]] = {}

    for row in active_grid.itertuples(index=False):
        sc = str(row.store_code)
        ic = str(row.item_code)
        s_info = store_meta.get(sc)
        if not s_info:
            continue
        u_price = float(sku_meta_map.get(ic, {}).get('unit_price', 0.0))

        if row.donor_excess > 0:
            ea = excess_store_agg.setdefault(sc, {
                'store_code': sc,
                'store_name': s_info['name'],
                'district': s_info['district'],
                'excess_sku_count': 0,
                'excess_units': 0,
                'aged_60d_units': 0,
                'excess_value': 0.0,
                'recommended_transfer_out': 0,
                'ageing_reduction_value': 0.0,
            })
            ea['excess_sku_count'] += 1
            ea['excess_units'] += int(row.donor_excess)
            ea['aged_60d_units'] += int(min(row.donor_excess, row.aged_60_int))
            ea['excess_value'] += float(row.donor_excess) * u_price

        if row.receiver_deficit > 0:
            da = deficit_store_agg.setdefault(sc, {
                'store_code': sc,
                'store_name': s_info['name'],
                'district': s_info['district'],
                'deficit_sku_count': 0,
                'deficit_units': 0,
                'recommended_receive_in': 0,
                'potential_sales_value': 0.0,
            })
            da['deficit_sku_count'] += 1
            da['deficit_units'] += int(row.receiver_deficit)

    # Strictly Same-District Eligibility: keep only (item_code, district) pairs (district != 'OTHER')
    # that have BOTH >=1 Excess Donor Store AND >=1 Deficit Receiver Store in that SAME district!
    valid_dist_grid = active_grid[active_grid['district'] != 'OTHER']
    if valid_dist_grid.empty:
        return empty_payload

    dist_sku_sides = valid_dist_grid.groupby(['item_code', 'district'], as_index=False).agg(
        tot_excess=('donor_excess', 'sum'),
        tot_deficit=('receiver_deficit', 'sum'),
    )
    eligible_pairs = dist_sku_sides[
        (dist_sku_sides['tot_excess'] > 0) & (dist_sku_sides['tot_deficit'] > 0)
    ][['item_code', 'district']]
    if eligible_pairs.empty:
        return empty_payload

    dual_grid = pd.merge(valid_dist_grid, eligible_pairs, on=['item_code', 'district'], how='inner')
    dual_grid['_imm_shortfall'] = np.maximum(
        0.0,
        dual_grid['fc_15d'].values - (dual_grid['on_hand_int'].values + dual_grid['in_transit_int'].values),
    )

    # Pre-sort all donors and all receivers ONCE globally (eliminates thousands of per-SKU DataFrame sort_values calls!)
    all_donors_df = dual_grid[dual_grid['donor_excess'] > 0].sort_values(
        ['aged_90_int', 'aged_60_int', 'donor_excess', 'avg_age'],
        ascending=[False, False, False, False],
    )
    all_receivers_df = dual_grid[dual_grid['receiver_deficit'] > 0].sort_values(
        ['_imm_shortfall', 'daily_fc', 'receiver_deficit'],
        ascending=[False, False, False],
    )

    donors_by_sku: Dict[str, List[Dict[str, Any]]] = {}
    for r in all_donors_df.itertuples(index=False):
        donors_by_sku.setdefault(str(r.item_code), []).append({
            'store_code': str(r.store_code),
            'store_name': str(r.store_name),
            'district': str(r.district),
            'current_stock': int(r.on_hand_int),
            'in_transit': int(r.in_transit_int),
            'd30': int(round(r.d30)),
            'fc_15d': round(float(r.fc_15d), 1),
            'fc_30d': round(float(r.fc_30d), 1),
            'safety_stock': int(r.safety_stock),
            'protected_stock': int(r.protected_stock),
            'coverage_days': int(round(r.coverage_days)),
            'aged_60_int': int(r.aged_60_int),
            'aged_90_int': int(r.aged_90_int),
            'avg_age': int(round(r.avg_age)),
            'rem_excess': int(r.donor_excess),
            'initial_excess': int(r.donor_excess),
            'transferred_out': 0,
        })

    receivers_by_sku: Dict[str, List[Dict[str, Any]]] = {}
    for r in all_receivers_df.itertuples(index=False):
        receivers_by_sku.setdefault(str(r.item_code), []).append({
            'store_code': str(r.store_code),
            'store_name': str(r.store_name),
            'district': str(r.district),
            'current_stock': int(r.on_hand_int),
            'in_transit': int(r.in_transit_int),
            'd30': int(round(r.d30)),
            'fc_15d': round(float(r.fc_15d), 1),
            'fc_30d': round(float(r.fc_30d), 1),
            'safety_stock': int(r.safety_stock),
            'required_stock': int(r.required_stock),
            'coverage_days': int(round(r.coverage_days)),
            'aged_60_int': int(r.aged_60_int),
            'avg_age': int(round(r.avg_age)),
            'rem_deficit': int(r.receiver_deficit),
            'initial_deficit': int(r.receiver_deficit),
            'received_in': 0,
        })

    # ── 4. Strictly Same-District Balanced Transfer Solver (Zero Cross-District Freight) ──
    transfer_products: List[Dict[str, Any]] = []
    all_routes: List[Dict[str, Any]] = []

    total_rec_qty = 0
    total_intra_qty = 0
    total_ageing_60_red = 0
    total_ageing_90_red = 0
    total_ageing_val_red = 0.0
    total_sales_opp_units = 0.0
    total_sales_opp_val = 0.0

    for ic_str, donors in donors_by_sku.items():
        receivers = receivers_by_sku.get(ic_str)
        if not receivers:
            continue
        meta = sku_meta_map.get(ic_str, {
            'code': ic_str, 'name': ic_str, 'product_line': 'OTHER', 'brand': 'GENERAL', 'unit_price': 0.0,
        })
        u_price = max(float(meta.get('unit_price', 0.0)), 0.0)

        sku_routes: List[Dict[str, Any]] = []

        # Single-Pass Strict Same-District Matching: d_node['district'] == r_node['district'] ONLY
        for d_node in donors:
            if d_node['rem_excess'] <= 0 or not d_node['district'] or d_node['district'] == 'OTHER':
                continue
            for r_node in receivers:
                if r_node['rem_deficit'] <= 0 or d_node['rem_excess'] <= 0:
                    continue
                if d_node['store_code'] == r_node['store_code']:
                    continue
                if d_node['district'] != r_node['district']:
                    continue

                qty = min(d_node['rem_excess'], r_node['rem_deficit'])
                if qty <= 0:
                    continue

                # Track how many aged 60+d units remain at donor prior to this leg
                rem_aged60_before = max(0, d_node['aged_60_int'] - d_node['transferred_out'])
                leg_aged60_red = min(qty, rem_aged60_before)

                # Track incremental unmet 30d demand captured by this leg at receiver
                eff_before_leg = r_node['current_stock'] + r_node['in_transit'] + r_node['received_in']
                unmet_30_rem = max(0.0, r_node['fc_30d'] - float(eff_before_leg))
                unmet_15_rem = max(0.0, r_node['fc_15d'] - float(eff_before_leg))
                leg_opp_units = min(float(qty), max(unmet_15_rem, unmet_30_rem * 0.85))
                if leg_opp_units < 0.5 and qty >= 1 and unmet_30_rem > 0:
                    leg_opp_units = min(float(qty), unmet_30_rem)

                d_node['rem_excess'] -= qty
                d_node['transferred_out'] += qty
                r_node['rem_deficit'] -= qty
                r_node['received_in'] += qty

                sku_routes.append({
                    'sku_code': ic_str,
                    'sku_name': meta['name'],
                    'brand': meta['brand'],
                    'product_line': meta['product_line'],
                    'from_store_code': d_node['store_code'],
                    'from_store': d_node['store_name'],
                    'from_district': d_node['district'],
                    'from_stock_before': d_node['current_stock'],
                    'from_protected_stock': d_node['protected_stock'],
                    'from_avg_age_days': d_node['avg_age'],
                    'to_store_code': r_node['store_code'],
                    'to_store': r_node['store_name'],
                    'to_district': r_node['district'],
                    'to_stock_before': r_node['current_stock'],
                    'to_fc_15d': r_node['fc_15d'],
                    'to_safety_stock': r_node['safety_stock'],
                    'transfer_qty': int(qty),
                    'route_scope': 'Same District',
                    'ageing_reduced_units': int(leg_aged60_red),
                    'ageing_reduced_val': round(float(leg_aged60_red) * u_price, 0),
                    'sales_opportunity_units': round(float(leg_opp_units), 1),
                    'sales_opportunity_val': round(float(leg_opp_units) * u_price, 0),
                })

        if not sku_routes:
            continue

        # Optional single-showroom focus filter: keep SKU if focus_store_code participates!
        if focus_store_code:
            participates = any(
                (rt['from_store_code'] == focus_store_code or rt['to_store_code'] == focus_store_code)
                for rt in sku_routes
            )
            if not participates:
                continue

        sku_transfer_qty = sum(rt['transfer_qty'] for rt in sku_routes)
        sku_intra_qty = sum(rt['transfer_qty'] for rt in sku_routes if rt['route_scope'] == 'Same District')

        # Compute exact SKU-level Ageing Reduction & Sales Opportunity across participating stores
        sku_age60_red = 0
        sku_age90_red = 0
        sku_opp_units = 0.0

        donor_actions: List[Dict[str, Any]] = []
        for d_node in donors:
            if d_node['transferred_out'] <= 0:
                continue
            tout = d_node['transferred_out']
            a60 = min(tout, d_node['aged_60_int'])
            a90 = min(tout, d_node['aged_90_int'])
            sku_age60_red += a60
            sku_age90_red += a90

            if d_node['store_code'] in excess_store_agg:
                excess_store_agg[d_node['store_code']]['recommended_transfer_out'] += tout
                excess_store_agg[d_node['store_code']]['ageing_reduction_value'] += float(a60) * u_price

            donor_actions.append({
                'store_code': d_node['store_code'],
                'store_name': d_node['store_name'],
                'district': d_node['district'],
                'current_stock': d_node['current_stock'],
                'in_transit': d_node['in_transit'],
                'd30': d_node['d30'],
                'forecast_15d': int(round(d_node['fc_15d'])),
                'safety_stock': d_node['safety_stock'],
                'protected_or_required': d_node['protected_stock'],
                'post_transfer_stock': d_node['current_stock'] - tout,
                'aged_60d_qty': d_node['aged_60_int'],
                'avg_age_days': d_node['avg_age'],
                'action_type': 'Transfer',
                'action_qty': tout,
                'recommended_action': f"Transfer {tout}",
            })

        receiver_actions: List[Dict[str, Any]] = []
        for r_node in receivers:
            if r_node['received_in'] <= 0:
                continue
            rin = r_node['received_in']
            eff_sup = float(r_node['current_stock'] + r_node['in_transit'])
            unmet_15 = max(0.0, r_node['fc_15d'] - eff_sup)
            unmet_30 = max(0.0, r_node['fc_30d'] - eff_sup)
            opp_u = min(float(rin), max(unmet_15, unmet_30 * 0.85))
            if opp_u < 0.5 and unmet_30 > 0:
                opp_u = min(float(rin), unmet_30)
            sku_opp_units += opp_u

            if r_node['store_code'] in deficit_store_agg:
                deficit_store_agg[r_node['store_code']]['recommended_receive_in'] += rin
                deficit_store_agg[r_node['store_code']]['potential_sales_value'] += opp_u * u_price

            receiver_actions.append({
                'store_code': r_node['store_code'],
                'store_name': r_node['store_name'],
                'district': r_node['district'],
                'current_stock': r_node['current_stock'],
                'in_transit': r_node['in_transit'],
                'd30': r_node['d30'],
                'forecast_15d': int(round(r_node['fc_15d'])),
                'safety_stock': r_node['safety_stock'],
                'protected_or_required': r_node['required_stock'],
                'post_transfer_stock': r_node['current_stock'] + rin,
                'aged_60d_qty': r_node['aged_60_int'],
                'avg_age_days': r_node['avg_age'],
                'action_type': 'Receive',
                'action_qty': rin,
                'recommended_action': f"Receive {rin}",
            })

        donor_actions.sort(key=lambda a: (a['action_qty'], a['aged_60d_qty']), reverse=True)
        receiver_actions.sort(key=lambda a: (a['action_qty'], a['forecast_15d']), reverse=True)

        # Group store_actions by district (ranked by district transfer volume DESC) so each district's
        # local Transfer donors appear immediately above that exact same district's Receive recipients!
        dist_vol: Dict[str, int] = {}
        for a in donor_actions:
            dist_vol[a['district']] = dist_vol.get(a['district'], 0) + int(a['action_qty'])
        ordered_dists = sorted(dist_vol.keys(), key=lambda d: dist_vol[d], reverse=True)

        store_actions: List[Dict[str, Any]] = []
        for dist_name in ordered_dists:
            d_sub = [a for a in donor_actions if a['district'] == dist_name]
            r_sub = [a for a in receiver_actions if a['district'] == dist_name]
            if len(d_sub) > 3:
                store_actions.extend(d_sub[:3] + r_sub[:3] + d_sub[3:] + r_sub[3:])
            else:
                store_actions.extend(d_sub + r_sub)

        sku_routes.sort(key=lambda r: (r['transfer_qty'], r['sales_opportunity_val']), reverse=True)

        sku_age_val = round(float(sku_age60_red) * u_price, 0)
        sku_opp_val = round(float(sku_opp_units) * u_price, 0)

        total_rec_qty += sku_transfer_qty
        total_intra_qty += sku_intra_qty
        total_ageing_60_red += sku_age60_red
        total_ageing_90_red += sku_age90_red
        total_ageing_val_red += sku_age_val
        total_sales_opp_units += sku_opp_units
        total_sales_opp_val += sku_opp_val

        # Priority score balances captured sales revenue, ageing capital reduction, and unit volume
        impact_score = sku_opp_val + 0.85 * sku_age_val + sku_transfer_qty * max(u_price * 0.25, 250.0)

        transfer_products.append({
            'code': ic_str,
            'name': meta['name'],
            'brand': meta['brand'],
            'product_line': meta['product_line'],
            'unit_price': round(u_price, 0),
            'donor_stores_count': len(donor_actions),
            'receiver_stores_count': len(receiver_actions),
            'active_districts_count': len(ordered_dists),
            'top_district': ordered_dists[0] if ordered_dists else '',
            'total_excess_units': sum(d['initial_excess'] for d in donors),
            'total_deficit_units': sum(r['initial_deficit'] for r in receivers),
            'recommended_transfer_qty': int(sku_transfer_qty),
            'same_district_qty': int(sku_intra_qty),
            'ageing_reduction_units': int(sku_age60_red),
            'ageing_reduction_90d_units': int(sku_age90_red),
            'ageing_reduction_val': sku_age_val,
            'sales_opportunity_units': int(round(sku_opp_units)),
            'sales_opportunity_val': sku_opp_val,
            'impact_score': round(impact_score, 1),
            'store_actions': store_actions,
            'routes': sku_routes[:20],
        })
        all_routes.extend(sku_routes)

    # Sort transferable products by total economic impact (Sales Opportunity + Ageing Stock Reduction)
    transfer_products.sort(
        key=lambda x: (x['impact_score'], x['recommended_transfer_qty']),
        reverse=True,
    )

    # Sort routes by economic value
    all_routes.sort(
        key=lambda r: (r['sales_opportunity_val'] + r['ageing_reduced_val'], r['transfer_qty']),
        reverse=True,
    )

    # Pick default selected product for the Store Recommended Action Card (match selected_sku_hint if transferable!)
    selected_tp = None
    if selected_sku_hint:
        for tp in transfer_products:
            if tp['code'] == selected_sku_hint:
                selected_tp = tp
                break
    if selected_tp is None and transfer_products:
        # Prefer an illustrative multi-store product (>=3 participating stores like Store A -> Store B, Store C) among top 8!
        for tp in transfer_products[:8]:
            if len(tp['store_actions']) >= 3:
                selected_tp = tp
                break
        if selected_tp is None:
            selected_tp = transfer_products[0]

    # Build Excess Stores & Deficit Stores leaderboards
    excess_stores_list = list(excess_store_agg.values())
    if focus_store_code:
        excess_stores_list = [s for s in excess_stores_list if s['store_code'] == focus_store_code]
    excess_stores_list.sort(
        key=lambda s: (s['recommended_transfer_out'], s['aged_60d_units'], s['excess_units']),
        reverse=True,
    )
    for s in excess_stores_list:
        s['excess_value'] = round(s['excess_value'], 0)
        s['ageing_reduction_value'] = round(s['ageing_reduction_value'], 0)

    deficit_stores_list = list(deficit_store_agg.values())
    if focus_store_code:
        deficit_stores_list = [s for s in deficit_stores_list if s['store_code'] == focus_store_code]
    deficit_stores_list.sort(
        key=lambda s: (s['potential_sales_value'], s['recommended_receive_in'], s['deficit_units']),
        reverse=True,
    )
    for s in deficit_stores_list:
        s['potential_sales_value'] = round(s['potential_sales_value'], 0)

    intra_pct = round((float(total_intra_qty) / max(float(total_rec_qty), 1.0)) * 100.0, 1) if total_rec_qty > 0 else 0.0
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    return {
        'kpis': {
            'excess_stores_count': len(excess_stores_list),
            'excess_units_total': int(sum(s['excess_units'] for s in excess_stores_list)),
            'deficit_stores_count': len(deficit_stores_list),
            'deficit_units_total': int(sum(s['deficit_units'] for s in deficit_stores_list)),
            'transferable_products_count': len(transfer_products),
            'recommended_transfer_qty': int(total_rec_qty),
            'intra_district_share_pct': intra_pct,
            'expected_ageing_reduction_units': int(total_ageing_60_red),
            'expected_ageing_reduction_90d_units': int(total_ageing_90_red),
            'expected_ageing_reduction_val': round(total_ageing_val_red, 0),
            'potential_sales_opportunity_units': int(round(total_sales_opp_units)),
            'potential_sales_opportunity_val': round(total_sales_opp_val, 0),
            'compute_ms': elapsed_ms,
        },
        'selected_transfer_product': selected_tp,
        'transfer_products': transfer_products[:max_products],
        'transfer_routes': all_routes[:500],
        'excess_stores': excess_stores_list[:20],
        'deficit_stores': deficit_stores_list[:20],
    }

