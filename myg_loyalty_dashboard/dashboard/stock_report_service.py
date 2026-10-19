import os
import time
import pandas as pd
from collections import defaultdict

try:
    from django.conf import settings
    BASE_DIR = getattr(settings, 'BASE_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
except Exception:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from .dashboard_api_logic import PRODUCT_CAT_MAPPING
except Exception:
    from dashboard.dashboard_api_logic import PRODUCT_CAT_MAPPING

_cached_results = {}
_bm_cache = None
_bm_cache_time = 0


def get_branch_master_df(force_refresh=False):
    """Fetches branch_master dataframe from ClickHouse and caches for 1 hour."""
    global _bm_cache, _bm_cache_time
    if not force_refresh and _bm_cache is not None and (time.time() - _bm_cache_time < 3600):
        return _bm_cache

    try:
        from analytics.clickhouse_service import get_ch_client
        ch = get_ch_client()
        bm = ch.query_df("SELECT code, branch_name, rbm, bdm, district FROM branch_master")
    except Exception as e:
        print("Warning: Could not fetch branch_master from ClickHouse:", e)
        bm = pd.DataFrame(columns=['code', 'branch_name', 'rbm', 'bdm', 'district'])

    bm['branch_name_clean'] = bm['branch_name'].astype(str).str.strip().str.upper()
    bm['rbm_clean'] = bm['rbm'].astype(str).str.strip()
    bm['bdm_clean'] = bm['bdm'].astype(str).str.strip()
    bm['district_clean'] = bm['district'].astype(str).str.strip()

    _bm_cache = bm
    _bm_cache_time = time.time()
    return _bm_cache


# ─── ClickHouse source: table `stock_ageing_report` (one row per stock line, per dated snapshot) ───
STOCK_CH_TABLE = 'stock_ageing_report'
_snapshot_cache = {'date': None, 'ts': 0.0}

# Valuation expressions on the ClickHouse columns (MOP/dealer fall back to cost when 0; MRP falls back to MOP when invalid)
CH_PRICE_EXPR = {
    'cost_price': 'cost_price',
    'dealer_price': 'if(dealer_price != 0, dealer_price, cost_price)',
    'mrp': 'if(mrp > 1000000 OR mrp <= 0, if(mop != 0, mop, cost_price), mrp)',
    'mop': 'if(mop != 0, mop, cost_price)',
}


def _get_ch():
    try:
        from analytics.clickhouse_service import get_ch_client
        return get_ch_client()
    except Exception as e:
        print("Warning: ClickHouse client unavailable for stock report:", e)
        return None


def get_latest_snapshot_date(ch=None, force_refresh=False):
    """Latest report_date in the ClickHouse stock_ageing_report table (cached 5 min).
    Returns None when ClickHouse/the table is unavailable or empty."""
    if not force_refresh and _snapshot_cache['date'] is not None and (time.time() - _snapshot_cache['ts'] < 300):
        return _snapshot_cache['date']
    ch = ch or _get_ch()
    if ch is None:
        return None
    try:
        latest, n = ch.query(f"SELECT max(report_date), count() FROM {STOCK_CH_TABLE}").result_rows[0]
    except Exception as e:
        print("Warning: could not read latest stock snapshot from ClickHouse:", e)
        return None
    if not n:
        return None
    _snapshot_cache['date'] = latest
    _snapshot_cache['ts'] = time.time()
    return latest


def _format_as_of(d):
    return f"{d.day} {d.strftime('%B %Y')}"


def _split_values(values, upper=False):
    if not values:
        return []
    if isinstance(values, str):
        values = values.split(',')
    out = [str(v).strip() for v in values if str(v).strip()]
    return [v.upper() for v in out] if upper else out


def _branch_allowlist(bm_df, rbm, bdm, district, branch):
    """Resolve RBM/BDM/District/Branch filters to a list of upper-cased branch names.
    Returns None when no filter is applied (no branch restriction)."""
    allowed = None
    mask = pd.Series(True, index=bm_df.index)
    bm_filtered = False
    for col, val in (('rbm_clean', rbm), ('bdm_clean', bdm), ('district_clean', district)):
        vals = _split_values(val)
        if vals:
            bm_filtered = True
            mask &= bm_df[col].isin(vals)
    if bm_filtered:
        allowed = set(bm_df.loc[mask, 'branch_name_clean'])
    br_vals = set(_split_values(branch, upper=True))
    if br_vals:
        allowed = br_vals if allowed is None else (allowed & br_vals)
    return None if allowed is None else sorted(allowed)


def _ch_bucket_select(price_expr):
    """SELECT list producing val_* (Cr) and qty_* ageing buckets, same aliases as the legacy SQL."""
    a = 'total_age'
    buckets = [
        ('lt_30', f'{a} < 30'),
        ('30_60', f'{a} >= 30 AND {a} < 60'),
        ('60_90', f'{a} >= 60 AND {a} <= 90'),
        ('30_90', f'{a} >= 30 AND {a} <= 90'),
        ('90_180', f'{a} > 90 AND {a} <= 180'),
        ('gt_180', f'{a} > 180'),
        ('180_365', f'{a} > 180 AND {a} <= 365'),
        ('gt_365', f'{a} > 365'),
    ]
    vals = [f"round(sumIf(stock * ({price_expr}), {c}) / 1e7, 2) AS val_{k}" for k, c in buckets]
    vals.append(f"round(sum(stock * ({price_expr})) / 1e7, 2) AS total_val")
    qtys = [f"sumIf(stock, {c}) AS qty_{k}" for k, c in buckets]
    qtys.append("sum(stock) AS total_qty")
    return ',\n        '.join(vals + qtys)


def _load_stock_frames_clickhouse(ch, snapshot, ch_price_expr, allowed_branches):
    """Aggregate the chosen snapshot in ClickHouse -> (df_prod, df_brand) with a `category` column."""
    where = ["report_date = {snap:Date}", "stock > 0"]
    params = {'snap': snapshot}
    if allowed_branches is not None:
        where.append("has({branches:Array(String)}, upper(trim(branch)))")
        params['branches'] = list(allowed_branches)
    where_sql = ' AND '.join(where)
    select = _ch_bucket_select(ch_price_expr)

    df_prod = ch.query_df(
        f"SELECT product,\n        {select}\nFROM {STOCK_CH_TABLE}\nWHERE {where_sql}\nGROUP BY product",
        parameters=params,
    )
    df_brand = ch.query_df(
        f"SELECT product, if(trim(brand) = '', 'OTHERS', trim(brand)) AS brand_name,\n        {select}\n"
        f"FROM {STOCK_CH_TABLE}\nWHERE {where_sql}\nGROUP BY product, brand_name",
        parameters=params,
    ).rename(columns={'brand_name': 'brand'})

    for df in (df_prod, df_brand):
        df['category'] = df['product'].map(PRODUCT_CAT_MAPPING).fillna('OTHER')
    df_prod = df_prod.sort_values(['category', 'total_val'], ascending=[True, False]).reset_index(drop=True)
    df_brand = df_brand.sort_values(['category', 'product', 'total_val'], ascending=[True, True, False]).reset_index(drop=True)
    return df_prod, df_brand


def get_stock_filter_options():
    """Returns available RBM, BDM, District, and Branch options for dropdowns (all from ClickHouse)."""
    bm_df = get_branch_master_df()
    rbms = sorted([str(x) for x in bm_df['rbm_clean'].unique() if str(x).strip()])
    bdms = sorted([str(x) for x in bm_df['bdm_clean'].unique() if str(x).strip()])
    districts = sorted([str(x) for x in bm_df['district_clean'].unique() if str(x).strip()])

    # Distinct branches of the latest stock_ageing_report snapshot
    branches = []
    try:
        ch = _get_ch()
        snap = get_latest_snapshot_date(ch) if ch is not None else None
        if snap is not None:
            rows = ch.query(
                f"SELECT DISTINCT upper(trim(branch)) FROM {STOCK_CH_TABLE} WHERE report_date = {{snap:Date}} AND branch != ''",
                parameters={'snap': snap},
            ).result_rows
            branches = sorted({r[0] for r in rows if r[0]})
    except Exception as e:
        print("Warning: could not load stock branch list from ClickHouse:", e)

    if not branches:
        # Still database-backed: fall back to the branch_master table
        branches = sorted([str(x) for x in bm_df['branch_name_clean'].unique() if str(x).strip()])

    return {
        'rbms': rbms,
        'bdms': bdms,
        'districts': districts,
        'branches': branches
    }


class StockDataUnavailable(RuntimeError):
    """Raised when the ClickHouse stock_ageing_report table cannot be read."""



def get_stock_ageing_data(force_refresh=False, rbm=None, bdm=None, branch=None, district=None, price_type='mop'):
    """
    Returns product-wise and category-wise stock ageing breakdown from the ClickHouse
    `stock_ageing_report` table (latest dated snapshot), optionally filtered by
    RBM, BDM, Branch, and District using branch_master.
    Supports valuation by:
      - 'mop': Market Operating Price (Default)
      - 'cost_price': Cost Price
      - 'dealer_price': Dealer Price
      - 'mrp': Maximum Retail Price
    """
    norm_price = (price_type or 'mop').lower().strip()
    if norm_price in ('cost', 'cost_price', 'cost price'):
        selected_price_type, price_label = 'cost_price', 'Cost Price'
    elif norm_price in ('dealer', 'dealer_price', 'dealer price', 'dp'):
        selected_price_type, price_label = 'dealer_price', 'Dealer Price'
    elif norm_price in ('mrp',):
        selected_price_type, price_label = 'mrp', 'MRP'
    else:  # default 'mop'
        selected_price_type, price_label = 'mop', 'MOP'

    ch_price_expr = CH_PRICE_EXPR[selected_price_type]

    cache_key = (
        force_refresh,
        str(rbm or '').strip(),
        str(bdm or '').strip(),
        str(branch or '').strip(),
        str(district or '').strip(),
        selected_price_type
    )

    if not force_refresh and cache_key in _cached_results:
        res, ts = _cached_results[cache_key]
        if time.time() - ts < 600:
            return res

    ch = _get_ch()
    if ch is None:
        raise StockDataUnavailable("ClickHouse is unreachable - stock ageing data cannot be loaded.")
    snapshot = get_latest_snapshot_date(ch, force_refresh)
    if snapshot is None:
        raise StockDataUnavailable(f"No snapshot found in ClickHouse table `{STOCK_CH_TABLE}`.")

    bm_df = get_branch_master_df(force_refresh=force_refresh)
    allowed = _branch_allowlist(bm_df, rbm, bdm, district, branch)
    df_prod, df_brand = _load_stock_frames_clickhouse(ch, snapshot, ch_price_expr, allowed)

    return _assemble_stock_report(
        df_prod, df_brand,
        rbm=rbm, bdm=bdm, branch=branch, district=district,
        selected_price_type=selected_price_type, price_label=price_label,
        cache_key=cache_key, as_of_date=_format_as_of(snapshot), data_source='clickhouse',
    )



def _assemble_stock_report(df_prod, df_brand, *, rbm, bdm, branch, district,
                           selected_price_type, price_label, cache_key, as_of_date, data_source):
    """Build the Category -> Product -> Top-10-Brand tree and the API response from aggregated frames."""

    # Pre-index brands by (category, product) and by category
    prod_brands_map = defaultdict(list)
    cat_brands_agg = defaultdict(lambda: defaultdict(lambda: {
        'val_lt_30': 0.0, 'val_30_60': 0.0, 'val_60_90': 0.0, 'val_30_90': 0.0,
        'val_90_180': 0.0, 'val_gt_180': 0.0, 'val_180_365': 0.0, 'val_gt_365': 0.0, 'total_val': 0.0,
        'qty_lt_30': 0, 'qty_30_60': 0, 'qty_60_90': 0, 'qty_30_90': 0,
        'qty_90_180': 0, 'qty_gt_180': 0, 'qty_180_365': 0, 'qty_gt_365': 0, 'total_qty': 0
    }))

    for _, r in df_brand.iterrows():
        cat = r['category']
        prod = r['product']
        brand_name = r['brand']

        b_item = {
            'brand': brand_name,
            'val_lt_30': float(r['val_lt_30']),
            'val_30_60': float(r['val_30_60']),
            'val_60_90': float(r['val_60_90']),
            'val_30_90': float(r['val_30_90']),
            'val_90_180': float(r['val_90_180']),
            'val_gt_180': float(r['val_gt_180']),
            'val_180_365': float(r['val_180_365']),
            'val_gt_365': float(r['val_gt_365']),
            'total_val': float(r['total_val']),
            'qty_lt_30': int(r['qty_lt_30']),
            'qty_30_60': int(r['qty_30_60']),
            'qty_60_90': int(r['qty_60_90']),
            'qty_30_90': int(r['qty_30_90']),
            'qty_90_180': int(r['qty_90_180']),
            'qty_gt_180': int(r['qty_gt_180']),
            'qty_180_365': int(r['qty_180_365']),
            'qty_gt_365': int(r['qty_gt_365']),
            'total_qty': int(r['total_qty'])
        }
        prod_brands_map[(cat, prod)].append(b_item)

        # Aggregate for category level brands
        for k in ['val_lt_30', 'val_30_60', 'val_60_90', 'val_30_90', 'val_90_180', 'val_gt_180', 'val_180_365', 'val_gt_365', 'total_val']:
            cat_brands_agg[cat][brand_name][k] = round(cat_brands_agg[cat][brand_name][k] + b_item[k], 2)
        for k in ['qty_lt_30', 'qty_30_60', 'qty_60_90', 'qty_30_90', 'qty_90_180', 'qty_gt_180', 'qty_180_365', 'qty_gt_365', 'total_qty']:
            cat_brands_agg[cat][brand_name][k] += b_item[k]

    # Build hierarchical tree: Category -> Products (with Top 10 Brands)
    categories = {}
    cat_order = ['MOBILE', 'CE', 'LAPTOP', 'ACC', 'TABLET', 'RIG', 'DEMO', 'SPARE', 'OTHER']

    grand_total = {
        'val_lt_30': 0.0, 'val_30_60': 0.0, 'val_60_90': 0.0, 'val_30_90': 0.0,
        'val_90_180': 0.0, 'val_gt_180': 0.0, 'val_180_365': 0.0, 'val_gt_365': 0.0, 'total_val': 0.0,
        'qty_lt_30': 0, 'qty_30_60': 0, 'qty_60_90': 0, 'qty_30_90': 0,
        'qty_90_180': 0, 'qty_gt_180': 0, 'qty_180_365': 0, 'qty_gt_365': 0, 'total_qty': 0
    }

    for _, row in df_prod.iterrows():
        cat = row['category']
        prod_name = row['product'] or 'Unknown'

        if cat not in categories:
            categories[cat] = {
                'category': cat,
                'val_lt_30': 0.0, 'val_30_60': 0.0, 'val_60_90': 0.0, 'val_30_90': 0.0,
                'val_90_180': 0.0, 'val_gt_180': 0.0, 'val_180_365': 0.0, 'val_gt_365': 0.0, 'total_val': 0.0,
                'qty_lt_30': 0, 'qty_30_60': 0, 'qty_60_90': 0, 'qty_30_90': 0,
                'qty_90_180': 0, 'qty_gt_180': 0, 'qty_180_365': 0, 'qty_gt_365': 0, 'total_qty': 0,
                'products': [],
                'top_brands': []
            }

        # Top 10 brands for this specific product / subcategory
        top_brands_prod = sorted(prod_brands_map.get((cat, prod_name), []), key=lambda x: x['total_val'], reverse=True)[:10]

        prod_item = {
            'product': prod_name,
            'val_lt_30': float(row['val_lt_30']),
            'val_30_60': float(row['val_30_60']),
            'val_60_90': float(row['val_60_90']),
            'val_30_90': float(row['val_30_90']),
            'val_90_180': float(row['val_90_180']),
            'val_gt_180': float(row['val_gt_180']),
            'val_180_365': float(row['val_180_365']),
            'val_gt_365': float(row['val_gt_365']),
            'total_val': float(row['total_val']),
            'qty_lt_30': int(row['qty_lt_30']),
            'qty_30_60': int(row['qty_30_60']),
            'qty_60_90': int(row['qty_60_90']),
            'qty_30_90': int(row['qty_30_90']),
            'qty_90_180': int(row['qty_90_180']),
            'qty_gt_180': int(row['qty_gt_180']),
            'qty_180_365': int(row['qty_180_365']),
            'qty_gt_365': int(row['qty_gt_365']),
            'total_qty': int(row['total_qty']),
            'top_brands': top_brands_prod
        }
        categories[cat]['products'].append(prod_item)

        # Accumulate Category Totals
        for k in ['val_lt_30', 'val_30_60', 'val_60_90', 'val_30_90', 'val_90_180', 'val_gt_180', 'val_180_365', 'val_gt_365', 'total_val']:
            categories[cat][k] = round(categories[cat][k] + prod_item[k], 2)
            grand_total[k] = round(grand_total[k] + prod_item[k], 2)
        for k in ['qty_lt_30', 'qty_30_60', 'qty_60_90', 'qty_30_90', 'qty_90_180', 'qty_gt_180', 'qty_180_365', 'qty_gt_365', 'total_qty']:
            categories[cat][k] += prod_item[k]
            grand_total[k] += prod_item[k]

    # Attach category overall top 10 brands
    for cat_name, cat_obj in categories.items():
        b_list = []
        for b_name, b_data in cat_brands_agg.get(cat_name, {}).items():
            b_entry = {'brand': b_name}
            b_entry.update(b_data)
            b_list.append(b_entry)
        cat_obj['top_brands'] = sorted(b_list, key=lambda x: x['total_val'], reverse=True)[:10]

    # Sort categories according to priority order
    sorted_categories = []
    for c in cat_order:
        if c in categories:
            sorted_categories.append(categories.pop(c))
    for remaining in sorted(categories.values(), key=lambda x: x['total_val'], reverse=True):
        sorted_categories.append(remaining)

    result = {
        'status': 'success',
        'as_of_date': as_of_date,
        'data_source': data_source,
        'price_type': selected_price_type,
        'price_label': price_label,
        'categories': sorted_categories,
        'grand_total': grand_total,
        'filter_options': get_stock_filter_options(),
        'active_filters': {
            'rbm': rbm or '',
            'bdm': bdm or '',
            'branch': branch or '',
            'district': district or '',
            'price_type': selected_price_type
        }
    }
    _cached_results[cache_key] = (result, time.time())
    return result
