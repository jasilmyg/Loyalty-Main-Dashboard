"""
Growth & Degrowth Branch Analysis API Logic
Provides branch-level comparison: Sale Value, Qty, Unique Customers, New & Repeat

New & Repeat definition (correct loyalty approach):
  - NEW    = Customer's FIRST EVER purchase falls within the period
             (no purchase history before period start date)
  - REPEAT = Bought in the period AND had at least one purchase before period start date
"""

from django.core.cache import cache
import pandas as pd


def _get_ch():
    from analytics.clickhouse_service import get_ch_client
    return get_ch_client()


def _date_filter_sql(comp_type, val, year=2026, alias='s'):
    col = f"toDate({alias}.date)"
    if comp_type == 'monthly':
        return f"toMonth({col}) = {int(val)} AND toYear({col}) = {year}"
    elif comp_type == 'quarterly':
        q_map = {'JFM': [1,2,3], 'AMJ': [4,5,6], 'JAS': [7,8,9], 'OND': [10,11,12]}
        months = q_map.get(val, [1,2,3])
        m_list = ','.join(str(m) for m in months)
        return f"toMonth({col}) IN ({m_list}) AND toYear({col}) = {year}"
    elif comp_type == 'yearly':
        return f"toYear({col}) = {int(val)}"
    elif comp_type == 'fy':
        return f"{col} >= toDate('{int(val)}-04-01') AND {col} <= toDate('{int(val)+1}-03-31')"
    elif comp_type == 'custom':
        if '|' in val:
            start_date, end_date = val.split('|', 1)
            return f"{col} >= toDate('{start_date}') AND {col} <= toDate('{end_date}')"
        return "1=1"
    return "1=1"


def _get_period_start(comp_type, val, year):
    """Return the start date of a period as 'YYYY-MM-DD' string."""
    if comp_type == 'monthly':
        return f"{year}-{int(val):02d}-01"
    elif comp_type == 'quarterly':
        q_map = {'JFM': '01', 'AMJ': '04', 'JAS': '07', 'OND': '10'}
        return f"{year}-{q_map.get(val, '01')}-01"
    elif comp_type == 'yearly':
        return f"{int(val)}-01-01"
    elif comp_type == 'fy':
        return f"{int(val)}-04-01"
    elif comp_type == 'custom':
        if '|' in val:
            return val.split('|')[0].strip()
    return f"{year}-01-01"


def _in_clause(col, val_str):
    vals = [v.strip() for v in val_str.split(',') if v.strip()] if val_str else []
    if not vals:
        return None
    escaped = "','".join(v.replace("'", "''") for v in vals)
    return f"{col} IN ('{escaped}')"


def _resolve_branch_filter(ch, branch='', rbm='', bdm='', district='', state=''):
    """Resolve all secondary filters to branch codes, same as dashboard_api_logic."""
    from .utils import get_branch_mappings
    code_to_name, name_to_code = get_branch_mappings(ch)

    if branch:
        branch = ','.join([name_to_code.get(b.strip(), b.strip()) for b in branch.split(',')])

    if rbm:
        rbm_list = [r.strip() for r in rbm.split(',') if r.strip()]
        rbm_escaped = "','".join(r.replace("'", "''") for r in rbm_list)
        rows = ch.query(f"SELECT DISTINCT code FROM branch_master WHERE rbm IN ('{rbm_escaped}') AND code != ''").result_rows
        codes = [r[0] for r in rows]
        if codes:
            existing = [b for b in branch.split(',') if b] if branch else []
            branch = ','.join(list(set(existing + codes)) if existing else codes)

    if bdm:
        bdm_list = [b.strip() for b in bdm.split(',') if b.strip()]
        bdm_escaped = "','".join(b.replace("'", "''") for b in bdm_list)
        rows = ch.query(f"SELECT DISTINCT code FROM branch_master WHERE bdm IN ('{bdm_escaped}') AND code != ''").result_rows
        codes = [r[0] for r in rows]
        if codes:
            existing = [b for b in branch.split(',') if b] if branch else []
            branch = ','.join(list(set(existing + codes)) if existing else codes)

    if district:
        dist_list = [d.strip() for d in district.split(',') if d.strip()]
        dist_escaped = "','".join(d.replace("'", "''") for d in dist_list)
        rows = ch.query(f"SELECT DISTINCT code FROM branch_master WHERE district IN ('{dist_escaped}') AND code != ''").result_rows
        codes = [r[0] for r in rows]
        if codes:
            existing = [b for b in branch.split(',') if b] if branch else []
            branch = ','.join(list(set(existing + codes)) if existing else codes)

    if state:
        state_map_inv = {'Kerala': '32', 'Maharashtra': '27', 'Karnataka': '29', 'Puducherry': '34'}
        state_list = [s.strip() for s in state.split(',') if s.strip()]
        state_codes = [state_map_inv.get(s, s) for s in state_list]
        state_escaped = "','".join(s.replace("'", "''") for s in state_codes)
        rows = ch.query(f"SELECT DISTINCT code FROM branch_master WHERE substring(gst_no, 1, 2) IN ('{state_escaped}') AND code != ''").result_rows
        codes = [r[0] for r in rows]
        if codes:
            existing = [b for b in branch.split(',') if b] if branch else []
            branch = ','.join(list(set(existing + codes)) if existing else codes)

    return code_to_name, branch


def _fetch_branch_metrics(ch, comp_type, val, year, branch_filter='', brand_filter='', cat_filter='', product_filter=''):
    """Fetch sale value, qty, unique customers per branch for a given period."""
    date_cond = _date_filter_sql(comp_type, val, year)
    extra = [c for c in [
        _in_clause('m.brand',   brand_filter),
        _in_clause('s.branch',  branch_filter),
        _in_clause('m.product', product_filter),
    ] if c]
    where = f"toDate(s.date) != toDate('1970-01-01') AND {date_cond}"
    if extra:
        where += " AND " + " AND ".join(extra)

    sql = f"""
        SELECT
            if(isNull(s.branch) OR s.branch='', 'Unknown', s.branch) AS branch,
            sum(toFloat64(s.sold_price)) AS sale_value,
            sum(toFloat64(s.qty))        AS qty,
            countDistinct(i.customer_mobile) AS unique_customers
        FROM azure_sales_report s
        LEFT JOIN item_master m ON s.item_code = m.item_code
        LEFT JOIN azure_invoice_report i ON s.invoice_no = i.invoice_no
        WHERE {where}
        GROUP BY branch
    """
    rows = ch.query(sql).result_rows
    if not rows:
        return pd.DataFrame(columns=['branch', 'sale_value', 'qty', 'unique_customers'])
    df = pd.DataFrame(rows, columns=['branch', 'sale_value', 'qty', 'unique_customers'])
    df['sale_value']       = pd.to_numeric(df['sale_value'], errors='coerce').fillna(0)
    df['qty']              = pd.to_numeric(df['qty'], errors='coerce').fillna(0)
    df['unique_customers'] = pd.to_numeric(df['unique_customers'], errors='coerce').fillna(0).astype(int)
    return df


def _fetch_new_repeat_for_period(ch, period_cond, period_start_date, branch_filter='', brand_filter='', product_filter=''):
    """
    Correct loyalty definition for a single period:
      - NEW    = Customer's FIRST EVER purchase date falls within this period
                 (i.e., min(date) across ALL history >= period_start_date)
      - REPEAT = Bought in this period AND had at least one purchase BEFORE period_start_date

    Strategy:
      1. Get all distinct (branch, mobile) pairs who bought in this period.
      2. For each mobile, look up their first-ever purchase date globally.
      3. If first_ever_date >= period_start → NEW
         If first_ever_date <  period_start → REPEAT
    """
    branch_clause = _in_clause('s.branch', branch_filter)
    brand_clause  = _in_clause('m.brand',  brand_filter)
    prod_clause   = _in_clause('m.product', product_filter)

    extra_parts = [c for c in [branch_clause, brand_clause, prod_clause] if c]
    extra_sql   = (" AND " + " AND ".join(extra_parts)) if extra_parts else ""

    sql = f"""
        SELECT
            period_custs.branch        AS branch,
            period_custs.mobile        AS mobile,
            history.first_purchase_ever AS first_purchase_ever
        FROM (
            -- Step 1: Customers who bought in THIS period, per branch
            SELECT
                if(isNull(s.branch) OR s.branch = '', 'Unknown', s.branch) AS branch,
                i.customer_mobile AS mobile
            FROM azure_sales_report s
            LEFT JOIN item_master m ON s.item_code = m.item_code
            LEFT JOIN azure_invoice_report i ON s.invoice_no = i.invoice_no
            WHERE toDate(s.date) != toDate('1970-01-01')
              AND {period_cond}
              AND i.customer_mobile != ''
              AND i.customer_mobile IS NOT NULL
              {extra_sql}
            GROUP BY branch, mobile
        ) AS period_custs
        -- Step 2: Each customer's first-ever purchase date across ALL time
        LEFT JOIN (
            SELECT
                i2.customer_mobile        AS mobile,
                min(toDate(s2.date))      AS first_purchase_ever
            FROM azure_sales_report s2
            LEFT JOIN azure_invoice_report i2 ON s2.invoice_no = i2.invoice_no
            WHERE toDate(s2.date) != toDate('1970-01-01')
              AND i2.customer_mobile != ''
              AND i2.customer_mobile IS NOT NULL
            GROUP BY mobile
        ) AS history ON period_custs.mobile = history.mobile
    """

    rows = ch.query(sql).result_rows

    result = {}
    for branch, mobile, first_dt in rows:
        if branch not in result:
            result[branch] = {'new_customers': 0, 'repeat_customers': 0}
        # NEW if first purchase ever is on or after the period start date
        if first_dt is None or str(first_dt) >= period_start_date:
            result[branch]['new_customers'] += 1
        else:
            result[branch]['repeat_customers'] += 1

    return result


def _fetch_new_repeat(ch, comp_type, base_val, base_year, comp_val, comp_year,
                      branch_filter='', brand_filter='', product_filter=''):
    """
    Fetch new & repeat customers for both base and comp periods using
    the correct loyalty definition (first-ever purchase date).
    """
    comp_cond  = _date_filter_sql(comp_type, comp_val, comp_year)
    base_cond  = _date_filter_sql(comp_type, base_val, base_year)
    comp_start = _get_period_start(comp_type, comp_val, comp_year)
    base_start = _get_period_start(comp_type, base_val, base_year)

    comp_classified = _fetch_new_repeat_for_period(
        ch, comp_cond, comp_start, branch_filter, brand_filter, product_filter)
    base_classified = _fetch_new_repeat_for_period(
        ch, base_cond, base_start, branch_filter, brand_filter, product_filter)

    all_branches = set(comp_classified.keys()) | set(base_classified.keys())

    records = []
    for branch in all_branches:
        c = comp_classified.get(branch, {'new_customers': 0, 'repeat_customers': 0})
        b = base_classified.get(branch, {'new_customers': 0, 'repeat_customers': 0})
        records.append({
            'branch':             branch,
            'new_customers':      c['new_customers'],
            'repeat_customers':   c['repeat_customers'],
            'b_new_customers':    b['new_customers'],
            'b_repeat_customers': b['repeat_customers'],
        })

    if not records:
        return pd.DataFrame(columns=['branch', 'new_customers', 'repeat_customers',
                                     'b_new_customers', 'b_repeat_customers'])
    return pd.DataFrame(records)


def build_growth_degrowth_response(request):
    comp_type = request.GET.get('type',      'monthly')
    base_val  = request.GET.get('base',      '4')
    comp_val  = request.GET.get('comp',      '5')
    base_year = int(request.GET.get('base_year', 2026))
    comp_year = int(request.GET.get('comp_year', 2026))
    cat       = request.GET.get('category',  '')
    product   = request.GET.get('product',   '')
    brand     = request.GET.get('brand',     '')
    branch    = request.GET.get('branch',    '')
    rbm       = request.GET.get('rbm',       '')
    bdm       = request.GET.get('bdm',       '')
    district  = request.GET.get('district',  '')
    state     = request.GET.get('state',     '')

    if comp_type == 'custom':
        if not base_val or '|' not in base_val or not comp_val or '|' not in comp_val:
            raise ValueError('Date range not ready. Please wait for the date picker to initialize and try again.')

    cache_key = f"gd_v2_{comp_type}_{base_val}_{comp_val}_{base_year}_{comp_year}_{cat}_{product}_{brand}_{branch}_{rbm}_{bdm}_{district}_{state}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    ch = _get_ch()
    code_to_name, resolved_branch = _resolve_branch_filter(ch, branch, rbm, bdm, district, state)

    # Fetch branch metrics for base and comp periods
    df_b = _fetch_branch_metrics(ch, comp_type, base_val, base_year, resolved_branch, brand, cat, product)
    df_c = _fetch_branch_metrics(ch, comp_type, comp_val, comp_year, resolved_branch, brand, cat, product)

    # Map branch codes to names
    if not df_b.empty:
        df_b['branch'] = df_b['branch'].map(code_to_name).fillna(df_b['branch'])
    if not df_c.empty:
        df_c['branch'] = df_c['branch'].map(code_to_name).fillna(df_c['branch'])

    # Fetch new/repeat customers using correct first-purchase logic
    df_nr = _fetch_new_repeat(ch, comp_type, base_val, base_year, comp_val, comp_year,
                               resolved_branch, brand, product)
    if not df_nr.empty:
        df_nr['branch'] = df_nr['branch'].map(code_to_name).fillna(df_nr['branch'])

    # Rename base/comp columns
    df_b = df_b.rename(columns={'sale_value': 'b_rev', 'qty': 'b_qty', 'unique_customers': 'b_unique_cust'})
    df_c = df_c.rename(columns={'sale_value': 'c_rev', 'qty': 'c_qty', 'unique_customers': 'c_unique_cust'})

    # Merge all data
    merged = pd.merge(df_b, df_c, on='branch', how='outer').fillna(0)
    if not df_nr.empty:
        merged = pd.merge(merged, df_nr, on='branch', how='left').fillna(0)
    else:
        merged['new_customers']      = 0
        merged['repeat_customers']   = 0
        merged['b_new_customers']    = 0
        merged['b_repeat_customers'] = 0

    for col in ['new_customers', 'repeat_customers', 'b_new_customers', 'b_repeat_customers']:
        merged[col] = merged[col].astype(int)

    # Sort by comp revenue desc
    merged = merged.sort_values('c_rev', ascending=False)

    result = merged.to_dict('records')
    response = {'branches': result}
    cache.set(cache_key, response, timeout=1800)
    return response
