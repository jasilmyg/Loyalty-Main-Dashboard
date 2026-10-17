import os
import time
import duckdb
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

def get_parquet_path():
    candidates = [
        os.path.join(BASE_DIR, 'data', 'stock_report_16_oct.parquet'),
        os.path.join(BASE_DIR, '.cache', 'stock_report_16_oct.parquet'),
        os.path.join(os.path.dirname(BASE_DIR), 'data', 'stock_report_16_oct.parquet'),
        os.path.join(os.path.dirname(BASE_DIR), '.cache', 'stock_report_16_oct.parquet'),
        os.path.join(BASE_DIR, 'data', 'stock_report_15_oct.parquet'),
        os.path.join(BASE_DIR, '.cache', 'stock_report_15_oct.parquet'),
        os.path.join(os.path.dirname(BASE_DIR), 'data', 'stock_report_15_oct.parquet'),
        os.path.join(os.path.dirname(BASE_DIR), '.cache', 'stock_report_15_oct.parquet'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def get_excel_path():
    candidates = [
        os.path.join(BASE_DIR, 'stock_report_16_October_2026_all_products.xlsx'),
        os.path.join(os.path.dirname(BASE_DIR), 'stock_report_16_October_2026_all_products.xlsx'),
        os.path.join(os.path.dirname(BASE_DIR), 'stock_report_15_October_2026_all_products.xlsx'),
        os.path.join(BASE_DIR, 'stock_report_15_October_2026_all_products.xlsx'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

_cached_results = {}
_bm_cache = None
_bm_cache_time = 0


def _format_in_clause(values):
    """Formats a string or list of values into a safe SQL IN ('a', 'b') clause."""
    if not values:
        return None
    if isinstance(values, str):
        values = [v.strip() for v in values.split(',') if v.strip()]
    if not values:
        return None
    escaped = [v.replace("'", "''") for v in values]
    return "('" + "', '".join(escaped) + "')"


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


def get_stock_filter_options():
    """Returns available RBM, BDM, District, and Branch options for dropdowns."""
    bm_df = get_branch_master_df()
    rbms = sorted([str(x) for x in bm_df['rbm_clean'].unique() if str(x).strip()])
    bdms = sorted([str(x) for x in bm_df['bdm_clean'].unique() if str(x).strip()])
    districts = sorted([str(x) for x in bm_df['district_clean'].unique() if str(x).strip()])

    # Get distinct branches from the stock report parquet file
    branches = []
    p_path = get_parquet_path()
    if os.path.exists(p_path):
        try:
            con = duckdb.connect()
            duck_p = p_path.replace('\\', '/')
            b_list = con.execute(f"SELECT DISTINCT TRIM(UPPER(\"BRANCH\")) as b FROM '{duck_p}' WHERE \"BRANCH\" IS NOT NULL").df()['b'].dropna().tolist()
            branches = sorted(list(set(b_list)))
            con.close()
        except Exception:
            branches = sorted([str(x) for x in bm_df['branch_name_clean'].unique() if str(x).strip()])
    else:
        branches = sorted([str(x) for x in bm_df['branch_name_clean'].unique() if str(x).strip()])

    return {
        'rbms': rbms,
        'bdms': bdms,
        'districts': districts,
        'branches': branches
    }


def get_stock_ageing_data(force_refresh=False, rbm=None, bdm=None, branch=None, district=None, price_type='mop'):
    """
    Returns product-wise and category-wise stock ageing breakdown,
    optionally filtered by RBM, BDM, Branch, and District using branch_master.
    Supports valuation by:
      - 'mop': Market Operating Price (Default)
      - 'cost_price': Cost Price
      - 'dealer_price': Dealer Price
      - 'mrp': Maximum Retail Price
    """
    norm_price = (price_type or 'mop').lower().strip()
    if norm_price in ('cost', 'cost_price', 'cost price'):
        selected_price_type = 'cost_price'
        price_expr = 'COALESCE(s."COST PRICE", 0)'
        price_label = 'Cost Price'
    elif norm_price in ('dealer', 'dealer_price', 'dealer price', 'dp'):
        selected_price_type = 'dealer_price'
        price_expr = 'COALESCE(NULLIF(s."Dealer Price", 0), s."COST PRICE", 0)'
        price_label = 'Dealer Price'
    elif norm_price in ('mrp',):
        selected_price_type = 'mrp'
        price_expr = 'CASE WHEN s."MRP" > 1000000 OR s."MRP" <= 0 THEN COALESCE(NULLIF(s."MOP", 0), s."COST PRICE", 0) ELSE s."MRP" END'
        price_label = 'MRP'
    else:  # default 'mop'
        selected_price_type = 'mop'
        price_expr = 'COALESCE(NULLIF(s."MOP", 0), s."COST PRICE", 0)'
        price_label = 'MOP'

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

    # Ensure parquet cache exists
    parquet_path = get_parquet_path()
    if not os.path.exists(parquet_path):
        excel_path = get_excel_path()
        if not os.path.exists(excel_path):
            raise FileNotFoundError(f"Stock report data not found. Looked for parquet at {parquet_path} and excel at {excel_path}")
        os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
        import python_calamine
        reader = python_calamine.CalamineWorkbook.from_path(excel_path)
        sheet = reader.get_sheet_by_name("Detail")
        data = sheet.to_python()
        headers = data[0]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=headers)
        for num_col in ['STOCK', 'COST PRICE', 'PURCHASE PRICE', 'Dealer Price', 'MOP', 'KA MOP', 'MRP', 'TOTAL AGE', 'BRANCH AGE']:
            if num_col in df.columns:
                df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0)
        df.to_parquet(parquet_path, index=False, compression='zstd')

    parquet_duck = parquet_path.replace('\\', '/')

    bm_df = get_branch_master_df(force_refresh=force_refresh)

    con = duckdb.connect()
    cat_df = pd.DataFrame(list(PRODUCT_CAT_MAPPING.items()), columns=['PRODUCT', 'CAT'])
    con.register('cat_map', cat_df)
    con.register('bm', bm_df)

    # Build WHERE filter clauses
    where_parts = ['s."STOCK" > 0']

    r_c = _format_in_clause(rbm)
    if r_c:
        where_parts.append(f"bm.rbm_clean IN {r_c}")

    b_c = _format_in_clause(bdm)
    if b_c:
        where_parts.append(f"bm.bdm_clean IN {b_c}")

    d_c = _format_in_clause(district)
    if d_c:
        where_parts.append(f"bm.district_clean IN {d_c}")

    if branch:
        br_vals = [v.strip().upper() for v in branch.split(',') if v.strip()] if isinstance(branch, str) else [v.strip().upper() for v in branch]
        br_c = _format_in_clause(br_vals)
        if br_c:
            where_parts.append(f"(UPPER(TRIM(s.\"BRANCH\")) IN {br_c} OR bm.branch_name_clean IN {br_c})")

    where_sql = " AND ".join(where_parts)

    # 1. Product-level aggregation
    q_prod = f"""
    SELECT 
        COALESCE(m.CAT, 'OTHER') as category,
        s."PRODUCT" as product,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" < 30 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_lt_30,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" < 60 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_30_60,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" >= 60 AND s."TOTAL AGE" <= 90 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_60_90,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" <= 90 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_30_90,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 90 AND s."TOTAL AGE" <= 180 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_90_180,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 180 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_gt_180,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 180 AND s."TOTAL AGE" <= 365 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_180_365,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 365 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_gt_365,
        ROUND(SUM(s."STOCK" * ({price_expr})) / 1e7, 2) as total_val,
        
        SUM(CASE WHEN s."TOTAL AGE" < 30 THEN s."STOCK" ELSE 0 END) as qty_lt_30,
        SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" < 60 THEN s."STOCK" ELSE 0 END) as qty_30_60,
        SUM(CASE WHEN s."TOTAL AGE" >= 60 AND s."TOTAL AGE" <= 90 THEN s."STOCK" ELSE 0 END) as qty_60_90,
        SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" <= 90 THEN s."STOCK" ELSE 0 END) as qty_30_90,
        SUM(CASE WHEN s."TOTAL AGE" > 90 AND s."TOTAL AGE" <= 180 THEN s."STOCK" ELSE 0 END) as qty_90_180,
        SUM(CASE WHEN s."TOTAL AGE" > 180 THEN s."STOCK" ELSE 0 END) as qty_gt_180,
        SUM(CASE WHEN s."TOTAL AGE" > 180 AND s."TOTAL AGE" <= 365 THEN s."STOCK" ELSE 0 END) as qty_180_365,
        SUM(CASE WHEN s."TOTAL AGE" > 365 THEN s."STOCK" ELSE 0 END) as qty_gt_365,
        SUM(s."STOCK") as total_qty
    FROM '{parquet_duck}' s
    LEFT JOIN cat_map m ON s."PRODUCT" = m."PRODUCT"
    LEFT JOIN bm ON UPPER(TRIM(s."BRANCH")) = bm.branch_name_clean
    WHERE {where_sql}
    GROUP BY 1, 2
    ORDER BY 1, total_val DESC
    """
    df_prod = con.execute(q_prod).df()

    # 2. Brand-level aggregation (grouped by category, product, brand)
    q_brand = f"""
    SELECT 
        COALESCE(m.CAT, 'OTHER') as category,
        s."PRODUCT" as product,
        COALESCE(NULLIF(TRIM(s."BRAND"), ''), 'OTHERS') as brand,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" < 30 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_lt_30,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" < 60 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_30_60,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" >= 60 AND s."TOTAL AGE" <= 90 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_60_90,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" <= 90 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_30_90,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 90 AND s."TOTAL AGE" <= 180 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_90_180,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 180 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_gt_180,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 180 AND s."TOTAL AGE" <= 365 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_180_365,
        ROUND(SUM(CASE WHEN s."TOTAL AGE" > 365 THEN s."STOCK" * ({price_expr}) ELSE 0 END) / 1e7, 2) as val_gt_365,
        ROUND(SUM(s."STOCK" * ({price_expr})) / 1e7, 2) as total_val,
        
        SUM(CASE WHEN s."TOTAL AGE" < 30 THEN s."STOCK" ELSE 0 END) as qty_lt_30,
        SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" < 60 THEN s."STOCK" ELSE 0 END) as qty_30_60,
        SUM(CASE WHEN s."TOTAL AGE" >= 60 AND s."TOTAL AGE" <= 90 THEN s."STOCK" ELSE 0 END) as qty_60_90,
        SUM(CASE WHEN s."TOTAL AGE" >= 30 AND s."TOTAL AGE" <= 90 THEN s."STOCK" ELSE 0 END) as qty_30_90,
        SUM(CASE WHEN s."TOTAL AGE" > 90 AND s."TOTAL AGE" <= 180 THEN s."STOCK" ELSE 0 END) as qty_90_180,
        SUM(CASE WHEN s."TOTAL AGE" > 180 THEN s."STOCK" ELSE 0 END) as qty_gt_180,
        SUM(CASE WHEN s."TOTAL AGE" > 180 AND s."TOTAL AGE" <= 365 THEN s."STOCK" ELSE 0 END) as qty_180_365,
        SUM(CASE WHEN s."TOTAL AGE" > 365 THEN s."STOCK" ELSE 0 END) as qty_gt_365,
        SUM(s."STOCK") as total_qty
    FROM '{parquet_duck}' s
    LEFT JOIN cat_map m ON s."PRODUCT" = m."PRODUCT"
    LEFT JOIN bm ON UPPER(TRIM(s."BRANCH")) = bm.branch_name_clean
    WHERE {where_sql}
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, total_val DESC
    """
    df_brand = con.execute(q_brand).df()
    con.close()

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
        'as_of_date': '16 October 2026',
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
