import os
import time
import pymysql
import pandas as pd
from collections import defaultdict
from django.conf import settings

from .dashboard_api_logic import PRODUCT_CAT_MAPPING
from .stock_report_service import (
    get_branch_master_df,
    _branch_allowlist,
    _assemble_stock_report,
    _format_as_of,
    get_stock_filter_options
)

SR_HOST = getattr(settings, 'STARROCKS_HOST', '') or os.environ.get('STARROCKS_HOST', '192.168.0.252')
SR_PORT = int(getattr(settings, 'STARROCKS_PORT', 9030) or os.environ.get('STARROCKS_PORT', '9030'))
SR_USER = getattr(settings, 'STARROCKS_USER', '') or os.environ.get('STARROCKS_USER', 'test1')
SR_PASS = getattr(settings, 'STARROCKS_PASSWORD', '') or os.environ.get('STARROCKS_PASSWORD', '1234')
SR_DB   = getattr(settings, 'STARROCKS_DATABASE', '') or os.environ.get('STARROCKS_DATABASE', 'Loyalty_Analytics')

SR_PRICE_EXPR = {
    'cost_price': 'cost_price',
    'dealer_price': 'if(dealer_price != 0, dealer_price, cost_price)',
    'mrp': 'if(mrp > 1000000 or mrp <= 0, if(mop != 0, mop, cost_price), mrp)',
    'mop': 'if(mop != 0, mop, cost_price)',
}

_sr_cached_results = {}
_sr_snapshot_cache = {'date': None, 'ts': 0.0}

def get_sr_connection():
    return pymysql.connect(
        host=SR_HOST,
        port=SR_PORT,
        user=SR_USER,
        password=SR_PASS,
        database=SR_DB,
        charset='utf8mb4',
        read_timeout=60,
        write_timeout=60,
        autocommit=True
    )

def get_latest_sr_snapshot_date(force_refresh=False):
    global _sr_snapshot_cache
    if not force_refresh and _sr_snapshot_cache['date'] is not None and (time.time() - _sr_snapshot_cache['ts'] < 300):
        return _sr_snapshot_cache['date']
    
    try:
        conn = get_sr_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT max(report_date) FROM stock_ageing_report WHERE stock > 0;")
            row = cur.fetchone()
            latest = row[0] if row else None
        conn.close()
        if latest:
            _sr_snapshot_cache['date'] = latest
            _sr_snapshot_cache['ts'] = time.time()
            return latest
    except Exception as e:
        print("Warning: could not read latest stock snapshot from StarRocks:", e)
    return None

def _sr_bucket_select(price_expr):
    p = price_expr
    a = 'total_age'
    return f"""
    round(sum(if({a} < 30, stock * ({p}), 0)) / 1e7, 2) AS val_lt_30,
    round(sum(if({a} >= 30 and {a} < 60, stock * ({p}), 0)) / 1e7, 2) AS val_30_60,
    round(sum(if({a} >= 60 and {a} <= 90, stock * ({p}), 0)) / 1e7, 2) AS val_60_90,
    round(sum(if({a} >= 30 and {a} <= 90, stock * ({p}), 0)) / 1e7, 2) AS val_30_90,
    round(sum(if({a} > 90 and {a} <= 180, stock * ({p}), 0)) / 1e7, 2) AS val_90_180,
    round(sum(if({a} > 180, stock * ({p}), 0)) / 1e7, 2) AS val_gt_180,
    round(sum(if({a} > 180 and {a} <= 365, stock * ({p}), 0)) / 1e7, 2) AS val_180_365,
    round(sum(if({a} > 365, stock * ({p}), 0)) / 1e7, 2) AS val_gt_365,
    round(sum(stock * ({p})) / 1e7, 2) AS total_val,
    cast(sum(if({a} < 30, stock, 0)) as signed) AS qty_lt_30,
    cast(sum(if({a} >= 30 and {a} < 60, stock, 0)) as signed) AS qty_30_60,
    cast(sum(if({a} >= 60 and {a} <= 90, stock, 0)) as signed) AS qty_60_90,
    cast(sum(if({a} >= 30 and {a} <= 90, stock, 0)) as signed) AS qty_30_90,
    cast(sum(if({a} > 90 and {a} <= 180, stock, 0)) as signed) AS qty_90_180,
    cast(sum(if({a} > 180, stock, 0)) as signed) AS qty_gt_180,
    cast(sum(if({a} > 180 and {a} <= 365, stock, 0)) as signed) AS qty_180_365,
    cast(sum(if({a} > 365, stock, 0)) as signed) AS qty_gt_365,
    cast(sum(stock) as signed) AS total_qty
    """

def get_starrocks_stock_ageing_data(force_refresh=False, rbm=None, bdm=None, branch=None, district=None, price_type='mop'):
    norm_price = (price_type or 'mop').lower().strip()
    if norm_price in ('cost', 'cost_price', 'cost price'):
        selected_price_type, price_label = 'cost_price', 'Cost Price'
    elif norm_price in ('dealer', 'dealer_price', 'dealer price', 'dp'):
        selected_price_type, price_label = 'dealer_price', 'Dealer Price'
    elif norm_price in ('mrp',):
        selected_price_type, price_label = 'mrp', 'MRP'
    else:
        selected_price_type, price_label = 'mop', 'MOP'

    sr_price_expr = SR_PRICE_EXPR[selected_price_type]

    cache_key = (
        force_refresh,
        str(rbm or '').strip(),
        str(bdm or '').strip(),
        str(branch or '').strip(),
        str(district or '').strip(),
        selected_price_type
    )

    if not force_refresh and cache_key in _sr_cached_results:
        res, ts = _sr_cached_results[cache_key]
        if time.time() - ts < 300:
            return res

    snapshot = get_latest_sr_snapshot_date(force_refresh)
    if snapshot is None:
        raise RuntimeError("No stock snapshot found in StarRocks table `stock_ageing_report`.")

    bm_df = get_branch_master_df(force_refresh=force_refresh)
    allowed = _branch_allowlist(bm_df, rbm, bdm, district, branch)

    where_clauses = ["stock > 0"]
    params = []
    
    if snapshot:
        where_clauses.append("report_date = %s")
        params.append(str(snapshot))

    if allowed is not None:
        placeholders = ', '.join(['%s'] * len(allowed))
        where_clauses.append(f"upper(trim(branch)) IN ({placeholders})")
        params.extend(list(allowed))

    where_sql = " AND ".join(where_clauses)
    select_sql = _sr_bucket_select(sr_price_expr)

    q_prod = f"""
    SELECT product,
           {select_sql}
    FROM stock_ageing_report
    WHERE {where_sql}
    GROUP BY product
    """

    q_brand = f"""
    SELECT product,
           if(trim(brand) = '', 'OTHERS', trim(brand)) AS brand,
           {select_sql}
    FROM stock_ageing_report
    WHERE {where_sql}
    GROUP BY product, if(trim(brand) = '', 'OTHERS', trim(brand))
    """

    conn = get_sr_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(q_prod, params)
            rows_prod = cur.fetchall()
            df_prod = pd.DataFrame(rows_prod)

            cur.execute(q_brand, params)
            rows_brand = cur.fetchall()
            df_brand = pd.DataFrame(rows_brand)
    finally:
        conn.close()

    if df_prod.empty:
        df_prod = pd.DataFrame(columns=['product', 'val_lt_30', 'val_30_60', 'val_60_90', 'val_30_90', 'val_90_180',
                                        'val_gt_180', 'val_180_365', 'val_gt_365', 'total_val',
                                        'qty_lt_30', 'qty_30_60', 'qty_60_90', 'qty_30_90', 'qty_90_180',
                                        'qty_gt_180', 'qty_180_365', 'qty_gt_365', 'total_qty', 'category'])
    else:
        df_prod['category'] = df_prod['product'].map(PRODUCT_CAT_MAPPING).fillna('OTHER')
        df_prod = df_prod.sort_values(['category', 'total_val'], ascending=[True, False]).reset_index(drop=True)

    if df_brand.empty:
        df_brand = pd.DataFrame(columns=['product', 'brand', 'val_lt_30', 'val_30_60', 'val_60_90', 'val_30_90', 'val_90_180',
                                         'val_gt_180', 'val_180_365', 'val_gt_365', 'total_val',
                                         'qty_lt_30', 'qty_30_60', 'qty_60_90', 'qty_30_90', 'qty_90_180',
                                         'qty_gt_180', 'qty_180_365', 'qty_gt_365', 'total_qty', 'category'])
    else:
        df_brand['category'] = df_brand['product'].map(PRODUCT_CAT_MAPPING).fillna('OTHER')
        df_brand = df_brand.sort_values(['category', 'product', 'total_val'], ascending=[True, True, False]).reset_index(drop=True)

    as_of_str = _format_as_of(snapshot) if hasattr(snapshot, 'day') else str(snapshot)
    
    report = _assemble_stock_report(
        df_prod, df_brand,
        rbm=rbm, bdm=bdm, branch=branch, district=district,
        selected_price_type=selected_price_type, price_label=price_label,
        cache_key=cache_key, as_of_date=as_of_str, data_source='starrocks'
    )
    _sr_cached_results[cache_key] = (report, time.time())
    return report
