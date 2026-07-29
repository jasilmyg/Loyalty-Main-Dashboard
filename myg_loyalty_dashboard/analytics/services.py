"""
services.py – Native PostgreSQL analytics backend.
All queries run directly on the PostgreSQL server via Django's database
connection (psycopg2). No DuckDB, no local row fetching.

v_sales_data view provides:
  "Date"            : native DATE
  "Customer Mobile" : VARCHAR, .0 suffix stripped
  "Total Value"     : NUMERIC
"""
import re
from django.db import connection

TABLE = 'v_sales_data'

VALID_MOBILE = """
    "Customer Mobile" IS NOT NULL
    AND "Customer Mobile" ~ '^[0-9]{10}$'
    AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
"""


def _parse_date(d_str):
    if not d_str:
        return None
    if re.match(r'^\d{2}-\d{2}-\d{4}$', d_str):
        d, m, y = d_str.split('-')
        return f'{y}-{m}-{d}'
    return d_str


def _q(query, params=None):
    with connection.cursor() as cur:
        cur.execute(query, params or [])
        return cur.fetchall()


def _q1(query, params=None):
    with connection.cursor() as cur:
        cur.execute(query, params or [])
        return cur.fetchone()


# ── ClickHouse helpers ───────────────────────────────────────────────────────
try:
    from analytics.clickhouse_service import ch_query as _ch_raw, is_ch_available as _ch_avail
    CLICKHOUSE_ENABLED = True
except Exception:
    CLICKHOUSE_ENABLED = False


def _ch_q(sql, params=None):
    """Run query on ClickHouse. Returns list of tuples like PG fetchall()."""
    from analytics.clickhouse_service import ch_query
    return ch_query(sql, params or {})


def _ch_q1(sql, params=None):
    """Run query on ClickHouse. Returns single row like PG fetchone()."""
    rows = _ch_q(sql, params)
    return rows[0] if rows else None


CH_VALID_MOBILE = """
    LENGTH(customer_mobile) = 10
    AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
    AND customer_mobile != ''
"""
# ─────────────────────────────────────────────────────────────────────────────


class AnalyticsService:
    using_native = True

    def __init__(self):
        pass

    def _build_where_clause(self, filters, prefix=''):
        conditions, params = [], []
        p = prefix

        start_date = _parse_date(filters.get('start_date'))
        end_date   = _parse_date(filters.get('end_date'))
        branch     = filters.get('branch')
        staff      = filters.get('staff')
        rbm        = filters.get('rbm')
        bdm        = filters.get('bdm')

        if branch and str(branch).strip().lower() in ('all branches', 'all', ''):
            branch = None

        if start_date:
            conditions.append('parsed_date >= %s::DATE')
            params.append(start_date)
        if end_date:
            conditions.append('parsed_date <= %s::DATE')
            params.append(end_date)
        if branch:
            conditions.append(f'UPPER({p}"Branch") = UPPER(%s)')
            params.append(branch)
        if staff:
            conditions.append(f'UPPER({p}"Staff") = UPPER(%s)')
            params.append(staff)
        if rbm:
            conditions.append(f'UPPER({p}"RBM") = UPPER(%s)')
            params.append(rbm)
        if bdm:
            conditions.append(f'UPPER({p}"BDM") = UPPER(%s)')
            params.append(bdm)

        return (' AND '.join(conditions) if conditions else '1=1'), params

    def _build_ch_where_clause(self, filters):
        """Build ClickHouse-compatible WHERE clause. Returns (sql_str, params_dict)."""
        conditions, params = [], {}

        start_date = _parse_date(filters.get('start_date'))
        end_date   = _parse_date(filters.get('end_date'))
        branch     = filters.get('branch')
        staff      = filters.get('staff')
        rbm        = filters.get('rbm')
        bdm        = filters.get('bdm')

        if branch and str(branch).strip().lower() not in ('all branches', 'all', ''):
            conditions.append("upper(branch) = upper({branch:String})")
            params['branch'] = branch
        if start_date:
            conditions.append("parsed_date >= {start_date:Date}")
            params['start_date'] = start_date
        if end_date:
            conditions.append("parsed_date <= {end_date:Date}")
            params['end_date'] = end_date
        if staff:
            conditions.append("upper(staff) = upper({staff:String})")
            params['staff'] = staff
        if rbm:
            conditions.append("upper(rbm) = upper({rbm:String})")
            params['rbm'] = rbm
        if bdm:
            conditions.append("upper(bdm) = upper({bdm:String})")
            params['bdm'] = bdm

        where = ' AND '.join(conditions) if conditions else '1=1'
        return where, params

    def _ch_is_filtered(self, filters):
        """Return True if any filter is active (i.e., not a global query)."""
        b = filters.get('branch', '')
        return bool(
            filters.get('start_date') or filters.get('end_date') or
            filters.get('staff') or filters.get('rbm') or filters.get('bdm') or
            (b and str(b).strip().lower() not in ('all branches', 'all', ''))
        )

    # ── Category Analysis ──────────────────────────────────────────────────────
    def get_category_analysis(self, filters):
        ch_where, params = self._build_ch_where_clause(filters)
        
        # Replace 'parsed_date' with our string date parsing for item_wise table
        ch_where = ch_where.replace("parsed_date", "toDate(parseDateTimeBestEffort(date))")
        
        sql = f"""
        SELECT 
            multiIf(
                prefix = 'MOB', 'Mobile',
                prefix = 'STY', 'Stationery',
                prefix = 'AC', 'Air Conditioner',
                prefix = 'TV', 'Television',
                prefix = 'WSM', 'Washing Machine',
                prefix = 'REF', 'Refrigerator',
                prefix = 'LAP', 'Laptop',
                prefix = 'SWA', 'Smart Watch',
                prefix = 'ACC', 'Accessories',
                prefix = 'MXI', 'Mixer',
                prefix = 'IRB', 'Iron Box',
                prefix = 'GAS', 'Gas Stove',
                prefix = 'FAN', 'Fan',
                prefix = 'FRY', 'Air Fryer',
                prefix = 'PERF', 'Perfume',
                prefix = 'SRV', 'Services',
                prefix = 'STB', 'Set Top Box / Sound Bar',
                prefix = 'GDC', 'Gadgets',
                prefix = 'PNG', 'PNG',
                prefix = 'ABGN', 'ABGN',
                prefix = 'CRC', 'CRC',
                prefix
            ) AS category,
            SUM(sold_price) AS revenue,
            SUM(qty) AS quantity
        FROM (
            SELECT 
                extract(item_code, '^([A-Za-z]+)') AS prefix,
                sold_price,
                qty
            FROM item_wise_sales_data
            WHERE {ch_where}
        )
        GROUP BY category
        ORDER BY revenue DESC
        """
        
        rows = _ch_q(sql, params)
        
        # Format for charts
        data = []
        for r in rows:
            data.append({
                'category': r[0],
                'revenue': float(r[1]) if r[1] else 0,
                'quantity': int(r[2]) if r[2] else 0
            })
            
        return data

    # ── Sales Overview ─────────────────────────────────────────────────────────
    def get_sales_overview(self, filters):
        import json, hashlib
        from django.core.cache import cache
        cache_key = 'sales_overview_' + hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            # ClickHouse: SUM/COUNT over 1.3Cr rows in <0.5s
            row = _ch_q1(f"""
                SELECT
                    SUM(total_value)              AS total_revenue,
                    COUNT(DISTINCT invoice_number) AS total_invoices
                FROM sales_data
                WHERE {ch_where}
            """, ch_params)
            tr = float(row[0] or 0) if row else 0
            ti = int(row[1] or 0)   if row else 0
            atv = tr / ti if ti > 0 else 0

            monthly = _ch_q(f"""
                SELECT
                    formatDateTime(toStartOfMonth(parsed_date), '%b %y') AS m_label,
                    SUM(total_value) AS revenue
                FROM sales_data
                WHERE {ch_where}
                GROUP BY toStartOfMonth(parsed_date), m_label
                ORDER BY toStartOfMonth(parsed_date) ASC
            """, ch_params)
            result = {
                'total_revenue':  tr,
                'total_invoices': ti,
                'atv':            atv,
                'monthly_trend':  [{'month': r[0], 'revenue': float(r[1] or 0)} for r in monthly],
            }
            cache.set(cache_key, result, 3600)
            return result
        except Exception as e:
            print(f"[CH] sales_overview ClickHouse error: {e}")
            return {'total_revenue': 0, 'total_invoices': 0, 'atv': 0, 'monthly_trend': []}

    # ── Customer Analytics ─────────────────────────────────────────────────────────
    def get_customer_analytics(self, filters):
        import json, hashlib
        from django.core.cache import cache
        cache_key = 'cust_analytics_' + hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            row = _ch_q1(f"""
                WITH customer_agg AS (
                    SELECT customer_mobile,
                           COUNT(DISTINCT parsed_date) AS visit_count,
                           SUM(total_value) AS spend
                    FROM sales_data WHERE {ch_where} AND {CH_VALID_MOBILE}
                    GROUP BY customer_mobile
                )
                SELECT SUM(spend), COUNT(DISTINCT customer_mobile), COUNTIf(visit_count > 1)
                FROM customer_agg
            """, ch_params)
            total_ltv        = float(row[0] or 0) if row else 0
            total_customers  = int(row[1] or 0)   if row else 0
            repeat_customers = int(row[2] or 0)   if row else 0
        except Exception as e:
            print(f"[CH] customer_analytics ClickHouse error: {e}")
            return {'total_customers': 0, 'new_customers': 0, 'repeat_customers': 0, 'repeat_rate': 0, 'monthly_new': [], 'monthly_repeat': []}

    # ── Frequency Distribution ──────────────────────────────────────────────────────
    def get_frequency_distribution(self, filters):
        import json, hashlib
        from django.core.cache import cache
        cache_key = 'freq_dist_' + hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                WITH customer_stats AS (
                    SELECT customer_mobile,
                           COUNT(DISTINCT parsed_date) AS visits,
                           SUM(total_value) AS revenue
                    FROM sales_data WHERE {ch_where} AND {CH_VALID_MOBILE}
                    GROUP BY customer_mobile
                ),
                bucketed AS (
                    SELECT multiIf(
                        visits=1,'1 Visit', visits=2,'2 Visits', visits=3,'3 Visits',
                        visits=4,'4 Visits', visits BETWEEN 5 AND 9,'5-9 Visits',
                        visits BETWEEN 10 AND 20,'10-20 Visits',
                        visits BETWEEN 21 AND 50,'21-50 Visits',
                        visits BETWEEN 51 AND 100,'51-100 Visits',
                        'Above 100 Visits') AS segment,
                        visits, revenue
                    FROM customer_stats
                )
                SELECT segment, COUNT() AS customers, SUM(revenue) AS net_revenue,
                    COUNT()*100.0/SUM(COUNT()) OVER() AS cust_pct,
                    SUM(revenue)*100.0/SUM(SUM(revenue)) OVER() AS rev_pct,
                    SUM(revenue)/COUNT() AS asp
                FROM bucketed GROUP BY segment
                ORDER BY multiIf(
                    segment='1 Visit',1, segment='2 Visits',2, segment='3 Visits',3,
                    segment='4 Visits',4, segment='5-9 Visits',5, segment='10-20 Visits',6,
                    segment='21-50 Visits',7, segment='51-100 Visits',8, 9)
            """, ch_params)
        except Exception as e:
            print(f"[CH] frequency_distribution ClickHouse error: {e}")
            return []

    # ── RFM ──────────────────────────────────────────────────────────────────
    def _get_rfm_base_cte(self, where_sql, params=None):
        """Raw PG CTE for RFM fallback — scans v_sales_data directly."""
        return f"""
            WITH rfm_base AS (
                SELECT "Customer Mobile" AS mobile,
                    MAX("Customer Name")              AS customer_name,
                    (CURRENT_DATE - MAX("Date"))::INT AS recency,
                    COUNT(DISTINCT "Date")            AS frequency,
                    SUM("Total Value")::FLOAT         AS monetary,
                    MAX("Date")                       AS last_visit
                FROM {TABLE}
                WHERE {where_sql}
                  AND "Customer Mobile" ~ '^[0-9]{{10}}$'
                GROUP BY "Customer Mobile"
            ),
            scored AS (
                SELECT *,
                    CASE WHEN recency<=90 THEN 5 WHEN recency<=180 THEN 4
                         WHEN recency<=365 THEN 3 WHEN recency<=730 THEN 2 ELSE 1 END AS r_score,
                    CASE WHEN frequency>=5 THEN 5 WHEN frequency=4 THEN 4
                         WHEN frequency=3 THEN 3 WHEN frequency=2 THEN 2 ELSE 1 END AS f_score,
                    NTILE(5) OVER (ORDER BY monetary ASC) AS m_score
                FROM rfm_base
            ),
            segmented AS (
                SELECT *,
                    r_score::TEXT||f_score::TEXT||m_score::TEXT AS rfm_code,
                    CASE
                        WHEN r_score>=4 AND f_score>=4 AND m_score>=4 THEN 'Champions'
                        WHEN r_score>=3 AND f_score>=3 AND m_score>=3 THEN 'Loyal'
                        WHEN r_score>=4 AND f_score<=2               THEN 'New'
                        WHEN r_score=2 AND f_score>=3 AND m_score>=3 THEN 'At Risk'
                        WHEN r_score=1                               THEN 'Lost'
                        ELSE 'Others'
                    END AS segment
                FROM scored
            )
        """

    def get_rfm_segments(self, filters):
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                WITH rfm_base AS (
                    SELECT customer_mobile AS mobile,
                           COUNT()            AS frequency,
                           SUM(total_value)   AS monetary,
                           dateDiff('day', max(parsed_date), today()) AS recency
                    FROM sales_data
                    WHERE {ch_where} AND {CH_VALID_MOBILE}
                    GROUP BY customer_mobile
                ),
                scored AS (
                    SELECT mobile, monetary,
                        multiIf(recency<=90,5, recency<=180,4, recency<=365,3, recency<=730,2, 1) AS r_score,
                        multiIf(frequency>=5,5, frequency=4,4, frequency=3,3, frequency=2,2, 1) AS f_score,
                        ntile(5) OVER (ORDER BY monetary ASC) AS m_score
                    FROM rfm_base
                ),
                segmented AS (
                    SELECT mobile, monetary,
                        multiIf(
                            r_score>=4 AND f_score>=4 AND m_score>=4, 'Champions',
                            r_score>=3 AND f_score>=3 AND m_score>=3, 'Loyal',
                            r_score>=4 AND f_score<=2,               'New',
                            r_score=2  AND f_score>=3 AND m_score>=3, 'At Risk',
                            r_score=1,                               'Lost',
                            'Others'
                        ) AS segment
                    FROM scored
                )
                SELECT segment, COUNT() AS count,
                       SUM(monetary) AS total_revenue, avg(monetary) AS avg_revenue
                FROM segmented GROUP BY segment ORDER BY count DESC
            """, ch_params)
            return [{'segment': r[0], 'count': r[1],
                     'total_revenue': float(r[2] or 0), 'avg_revenue': float(r[3] or 0)} for r in rows]
        except Exception as e:
            print(f"[CH] rfm_segments ClickHouse error: {e}")
            return []

    # ── Cohort Retention ─────────────────────────────────────────────────────
    def get_cohort_retention(self):
        from django.core.cache import cache
        _ck = 'cohort_retention_global'
        _cached = cache.get(_ck)
        if _cached is not None:
            return _cached

        # ClickHouse primary path
        try:
            rows = _ch_q("""
                WITH cohort_items AS (
                    SELECT customer_mobile,
                           formatDateTime(min(parsed_date), '%Y-%m') AS cohort_month
                    FROM sales_data
                    WHERE length(customer_mobile) = 10 AND customer_mobile != ''
                    GROUP BY customer_mobile
                ),
                user_activities AS (
                    SELECT s.customer_mobile, ci.cohort_month,
                           dateDiff('month', toDate(concat(ci.cohort_month, '-01')), s.parsed_date) AS month_number
                    FROM sales_data s
                    JOIN cohort_items ci ON s.customer_mobile = ci.customer_mobile
                    WHERE length(s.customer_mobile) = 10 AND s.customer_mobile != ''
                )
                SELECT cohort_month, month_number, COUNT(DISTINCT customer_mobile) AS num_users
                FROM user_activities
                GROUP BY cohort_month, month_number
                ORDER BY cohort_month, month_number
            """)
            cohorts = {}
            for row in rows:
                c_month, m_num, count = row
                if c_month not in cohorts:
                    cohorts[c_month] = {}
                cohorts[c_month][m_num] = count
            result = {'cohorts': cohorts}
            cache.set(_ck, result, 86400)
            return result
        except Exception as e:
            print(f"[CH] cohort_retention ClickHouse error: {e}")
            return {}

    # ── Payment & Discount (legacy columns not in view – return empty) ────────
    def get_payment_analytics(self, filters):    return {'distribution': {}}
    def get_discount_analysis(self, filters):   return {'distribution': {}}

    # ── Staff Performance ────────────────────────────────────────────────────
    def get_staff_performance(self, filters):
        where_sql, params = self._build_where_clause(filters)
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                SELECT staff, staff_code,
                    SUM(total_value) AS sales_value,
                    COUNT(DISTINCT invoice_number) AS invoice_count,
                    SUM(total_value) / nullIf(COUNT(DISTINCT invoice_number), 0) AS atv
                FROM sales_data
                WHERE {ch_where} AND staff != '' AND length(staff) > 0
                GROUP BY staff, staff_code
                ORDER BY sales_value DESC LIMIT 50
            """, ch_params)
        except Exception as e:
            print(f"[CH] staff_performance fallback: {e}")
            rows = _q(f"""
                SELECT "Staff", "Staff Code",
                    SUM("Total Value")::FLOAT AS sales_value,
                    COUNT(DISTINCT "Invoice Number") AS invoice_count,
                    SUM("Total Value")::FLOAT / NULLIF(COUNT(DISTINCT "Invoice Number"),0) AS atv
                FROM {TABLE}
                WHERE {where_sql} AND "Staff" IS NOT NULL AND "Staff" != ''
                GROUP BY "Staff","Staff Code"
                ORDER BY sales_value DESC NULLS LAST LIMIT 50
            """, params)
        return [{'staff':r[0],'code':r[1],'sales':float(r[2] or 0),'invoices':r[3],'atv':float(r[4] or 0)} for r in rows]

    def get_branch_performance(self, filters):
        where_sql, params = self._build_where_clause(filters)
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                SELECT branch,
                    SUM(total_value) AS revenue,
                    COUNT(DISTINCT invoice_number) AS transactions,
                    COUNT(DISTINCT customer_mobile) AS customer_count,
                    SUM(total_value) / nullIf(COUNT(DISTINCT invoice_number), 0) AS atv
                FROM sales_data
                WHERE {ch_where} AND branch != '' AND length(branch) > 0
                GROUP BY branch ORDER BY revenue DESC
            """, ch_params)
        except Exception as e:
            print(f"[CH] branch_performance fallback: {e}")
            rows = _q(f"""
                SELECT "Branch",
                    SUM("Total Value")::FLOAT AS revenue,
                    COUNT(DISTINCT "Invoice Number") AS transactions,
                    COUNT(DISTINCT "Customer Mobile") AS customer_count,
                    SUM("Total Value")::FLOAT / NULLIF(COUNT(DISTINCT "Invoice Number"),0) AS atv
                FROM {TABLE}
                WHERE {where_sql} AND "Branch" IS NOT NULL AND "Branch" != ''
                GROUP BY "Branch" ORDER BY revenue DESC NULLS LAST
            """, params)
        return [{'branch':r[0],'revenue':float(r[1] or 0),'transactions':r[2],'customers':r[3],'atv':float(r[4] or 0)} for r in rows]

    # ── Gap Analysis ─────────────────────────────────────────────────────────
    def get_gap_segmentation(self, filters):
        import json, hashlib
        from django.core.cache import cache

        cache_key = 'gap_segments_' + hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        where_sql, params = self._build_where_clause(filters)
        
        signals = {
            1: ('Very High','Immediate','Upsell immediately. Propose complementary products.'),
            2: ('High','High','Send gentle reminder (WhatsApp/SMS) about new stock.'),
            3: ('High','Medium','Campaign push. Feature limited-time collection.'),
            4: ('Medium','Medium','Offer / Bundle deal to stimulate visit.'),
            5: ('Medium','Critical','Win-back campaign. Personalized "We miss you" message.'),
            6: ('Low','Reactivate','Strong discount + emotional message tailored to past purchases.'),
            7: ('Very Low','Reactivate','Reactivation campaign. Ask for feedback or survey.'),
            8: ('Very Low','Ignore','Selective targeting only for high-value past spenders.'),
            9: ('Very Low','Ignore','Selective targeting. Low ROI probability.'),
            10: ('Very Low','Ignore','Ignore / Archive. Customer likely churned.'),
        }

        # ── ClickHouse primary path for all gap analysis (global + filtered) ──
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                WITH daily_visits AS (
                    SELECT customer_mobile AS mobile, parsed_date AS purchase_date
                    FROM sales_data
                    WHERE {ch_where} AND {CH_VALID_MOBILE}
                    GROUP BY customer_mobile, parsed_date
                ),
                ranked AS (
                    SELECT mobile, purchase_date,
                           lagInFrame(purchase_date) OVER(
                               PARTITION BY mobile ORDER BY purchase_date
                           ) AS prev_date
                    FROM daily_visits
                ),
                gaps AS (
                    SELECT mobile,
                           dateDiff('day', prev_date, purchase_date) AS gap_days
                    FROM ranked WHERE prev_date != toDate('1970-01-01')
                ),
                customer_avg_gaps AS (
                    SELECT mobile, avg(gap_days) AS gap_days FROM gaps GROUP BY mobile
                ),
                bucketed AS (
                    SELECT mobile, gap_days,
                        multiIf(
                            gap_days<=7,'1-7 Days', gap_days<=30,'8-30 Days',
                            gap_days<=60,'31-60 Days', gap_days<=90,'61-90 Days',
                            gap_days<=180,'91-180 Days', gap_days<=365,'180-365 Days',
                            gap_days<=730,'1-2 Years', gap_days<=1095,'2-3 Years',
                            gap_days<=1460,'3-4 Years','4+ Years'
                        ) AS gap_range,
                        multiIf(
                            gap_days<=7,1, gap_days<=30,2, gap_days<=60,3,
                            gap_days<=90,4, gap_days<=180,5, gap_days<=365,6,
                            gap_days<=730,7, gap_days<=1095,8, gap_days<=1460,9, 10
                        ) AS sort_order
                    FROM customer_avg_gaps
                )
                SELECT gap_range, COUNT(DISTINCT mobile) AS customers,
                    COUNT(DISTINCT mobile)*100.0/SUM(COUNT(DISTINCT mobile)) OVER() AS pct,
                    avg(gap_days) AS avg_gap, sort_order
                FROM bucketed GROUP BY gap_range, sort_order ORDER BY sort_order ASC
            """, ch_params)
        except Exception as e:
            print(f"[CH] gap_segmentation fallback: {e}")
            rows = _q(f"""
                WITH daily_visits AS (
                    SELECT "Customer Mobile" AS mobile, "Date" AS purchase_date
                    FROM {TABLE}
                    WHERE {where_sql} AND "Customer Mobile" ~ '^[0-9]{{10}}$'
                    GROUP BY "Customer Mobile","Date"
                ),
                ranked AS (
                    SELECT mobile, purchase_date,
                        LAG(purchase_date) OVER(PARTITION BY mobile ORDER BY purchase_date) AS prev_date
                    FROM daily_visits
                ),
                gaps AS (
                    SELECT mobile, (purchase_date - prev_date)::INT AS gap_days
                    FROM ranked WHERE prev_date IS NOT NULL
                ),
                customer_avg_gaps AS (
                    SELECT mobile, AVG(gap_days)::FLOAT AS gap_days FROM gaps GROUP BY mobile
                ),
                bucketed AS (
                    SELECT mobile, gap_days,
                        CASE
                            WHEN gap_days<=7    THEN '1-7 Days'   WHEN gap_days<=30   THEN '8-30 Days'
                            WHEN gap_days<=60   THEN '31-60 Days' WHEN gap_days<=90   THEN '61-90 Days'
                            WHEN gap_days<=180  THEN '91-180 Days' WHEN gap_days<=365  THEN '180-365 Days'
                            WHEN gap_days<=730  THEN '1-2 Years'  WHEN gap_days<=1095 THEN '2-3 Years'
                            WHEN gap_days<=1460 THEN '3-4 Years'  ELSE '4+ Years'
                        END AS gap_range,
                        CASE
                            WHEN gap_days<=7 THEN 1 WHEN gap_days<=30 THEN 2 WHEN gap_days<=60 THEN 3
                            WHEN gap_days<=90 THEN 4 WHEN gap_days<=180 THEN 5 WHEN gap_days<=365 THEN 6
                            WHEN gap_days<=730 THEN 7 WHEN gap_days<=1095 THEN 8 WHEN gap_days<=1460 THEN 9 ELSE 10
                        END AS sort_order
                    FROM customer_avg_gaps
                )
                SELECT gap_range, COUNT(DISTINCT mobile) AS customers,
                    COUNT(DISTINCT mobile)*100.0/SUM(COUNT(DISTINCT mobile)) OVER() AS pct,
                    AVG(gap_days)::FLOAT AS avg_gap, sort_order
                FROM bucketed GROUP BY gap_range, sort_order ORDER BY sort_order ASC
            """, params)
        result = [{
            'segment': r[0], 'count': r[1],
            'percentage': round(float(r[2] or 0), 2),
            'avg_gap': round(float(r[3] or 0), 1),
            'signal': signals.get(r[4], ('Medium','Medium',''))[0],
            'priority': signals.get(r[4], ('Medium','Medium',''))[1],
            'action': signals.get(r[4], ('Medium','Medium',''))[2],
        } for r in rows]
        cache.set(cache_key, result, 3600) # cache filtered view for 1 hour
        return result

    def get_customer_segmentation_matrix(self, filters):
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                WITH cs AS (
                    SELECT customer_mobile,
                           COUNT() AS visits,
                           SUM(total_value) AS total_spend,
                           dateDiff('day', max(parsed_date), today()) AS recency_days
                    FROM sales_data
                    WHERE {ch_where} AND {CH_VALID_MOBILE}
                    GROUP BY customer_mobile
                )
                SELECT
                    multiIf(visits=1,'One-Time', visits<=3,'Occasional','Frequent') AS freq_seg,
                    multiIf(recency_days<=90,'Active', recency_days<=365,'Lapsing','Inactive') AS rec_seg,
                    COUNT() AS customers, avg(total_spend) AS avg_spend
                FROM cs GROUP BY freq_seg, rec_seg ORDER BY freq_seg, rec_seg
            """, ch_params)
            return [{'freq':r[0],'recency':r[1],'customers':r[2],'avg_spend':round(float(r[3] or 0),2)} for r in rows]
        except Exception as e:
            print(f"[CH] segmentation_matrix ClickHouse error: {e}")
            return []

    # ── Loyalty KPIs ─────────────────────────────────────────────────────────
    def get_loyalty_overview_kpis(self, filters):
        import json, hashlib
        from django.core.cache import cache

        cache_key = 'loyalty_kpis_' + hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Always use ClickHouse (full 2020-2026 data available)
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            row = _ch_q1(f"""
                WITH daily_visits AS (
                    SELECT customer_mobile AS mobile, parsed_date AS purchase_date
                    FROM sales_data
                    WHERE {ch_where} AND {CH_VALID_MOBILE}
                    GROUP BY customer_mobile, parsed_date
                ),
                ranked AS (
                    SELECT mobile, purchase_date,
                           lagInFrame(purchase_date) OVER(
                               PARTITION BY mobile ORDER BY purchase_date
                           ) AS prev_date
                    FROM daily_visits
                ),
                gaps AS (
                    SELECT mobile, dateDiff('day', prev_date, purchase_date) AS gap_days
                    FROM ranked WHERE prev_date != toDate('1970-01-01')
                ),
                customer_avg_gaps AS (
                    SELECT mobile, avg(gap_days) AS avg_gap_days
                    FROM gaps GROUP BY mobile
                ),
                visit_counts AS (
                    SELECT mobile, COUNT() AS visits
                    FROM daily_visits GROUP BY mobile
                )
                SELECT
                    COUNT(DISTINCT v.mobile),
                    countIf(v.visits > 1),
                    -- FIX: ClickHouse LEFT JOIN returns 0.0 (not NULL) for unmatched
                    -- Float64 columns, so single-visit customers would be included
                    -- with avg_gap=0 and drag the average down incorrectly.
                    -- Use avgIf to only average repeat customers (visits > 1).
                    avgIf(g.avg_gap_days, v.visits > 1)
                FROM visit_counts v
                LEFT JOIN customer_avg_gaps g ON v.mobile = g.mobile
            """, ch_params)
            if row:
                total, repeat = int(row[0] or 0), int(row[1] or 0)
                result = {
                    'total_customers':  total,
                    'repeat_customers': repeat,
                    'repeat_rate':      round(repeat / total * 100, 1) if total else 0,
                    'avg_gap':          round(float(row[2] or 0), 1),
                }
                cache.set(cache_key, result, 86400)
                return result
        except Exception as e:
            print(f"[CH] loyalty_kpis error: {e}")

        result = {'total_customers': 0, 'repeat_customers': 0, 'repeat_rate': 0, 'avg_gap': 0}
        cache.set(cache_key, result, 3600)
        return result

    # ── Unique Branches ──────────────────────────────────────────────────────
    def get_unique_branches(self):
        rows = _q(f'SELECT DISTINCT "Branch" FROM {TABLE} WHERE "Branch" IS NOT NULL AND "Branch"!=\'\' ORDER BY "Branch"')
        return [r[0] for r in rows]

    # ── Invalid Mobiles ──────────────────────────────────────────────────────
    def get_invalid_mobiles(self):
        mob_invalid = """
            "Customer Mobile" IS NULL
            OR "Customer Mobile" = ''
            OR "Customer Mobile" !~ '^[0-9]{{10}}$'
        """
        total = (_q1(f'SELECT COUNT(*) FROM {TABLE} WHERE {mob_invalid}') or (0,))[0] or 0
        rows = _q(f"""
            SELECT
                COALESCE("Customer Mobile",'') AS raw_mobile,
                COALESCE("Customer Name",'')   AS customer_name,
                COALESCE("Branch",'')          AS branch,
                "Date"                         AS sale_date,
                COALESCE("Invoice Number",'')  AS invoice_number
            FROM {TABLE}
            WHERE {mob_invalid}
            ORDER BY sale_date DESC LIMIT 50000
        """)
        return {'total': total, 'rows': [
            {'raw_mobile':r[0],'customer_name':r[1],'branch':r[2],
             'sale_date':str(r[3]) if r[3] else '','invoice_number':r[4]}
            for r in rows
        ]}

    def _retail_sale_date_expr(self, alias='s'):
        a = f'{alias}.' if alias else ''
        return (
            f'(CASE WHEN SUBSTRING({a}"Date"::text, 5, 1) = \'-\' '
            f'THEN TO_DATE(SUBSTRING({a}"Date"::text, 1, 10), \'YYYY-MM-DD\') '
            f'WHEN SUBSTRING({a}"Date"::text, 3, 1) = \'-\' '
            f'THEN TO_DATE({a}"Date"::text, \'DD-MM-YYYY\') ELSE NULL END)'
        )

    def _retail_dim_sql(self, filters, alias='s'):
        a = f'{alias}.' if alias else ''
        parts, params = [], []
        branch = filters.get('branch')
        if branch and str(branch).strip().lower() not in ('all branches', 'all', ''):
            parts.append(f'UPPER({a}"Branch") = UPPER(%s)')
            params.append(branch)
        if filters.get('staff'):
            parts.append(f'UPPER({a}"Staff") = UPPER(%s)')
            params.append(filters['staff'])
        if filters.get('rbm'):
            parts.append(f'UPPER({a}"RBM") = UPPER(%s)')
            params.append(filters['rbm'])
        if filters.get('bdm'):
            parts.append(f'UPPER({a}"BDM") = UPPER(%s)')
            params.append(filters['bdm'])
        if not parts:
            return '', []
        return ' AND ' + ' AND '.join(parts), params

    def get_retail_loyalty_matrix(self, filters):
        """
        Retail Loyalty Performance Matrix.
        Primary: ClickHouse (fast for all filter combinations).
        Fallback: MVs then raw PG scan.
        Results are cached.
        """
        import json
        import hashlib
        from django.core.cache import cache

        cache_key = "retail_matrix2_" + hashlib.md5(json.dumps(filters, sort_keys=True).encode('utf-8')).hexdigest()
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        period = filters.get('period', 'monthly')
        start_date = _parse_date(filters.get('start_date'))
        end_date   = _parse_date(filters.get('end_date'))

        branch = filters.get('branch')
        has_branch = branch and str(branch).strip().lower() not in ('all branches', 'all', '')
        staff  = filters.get('staff')
        rbm    = filters.get('rbm')
        bdm    = filters.get('bdm')
        has_dim_filter = has_branch or staff or rbm or bdm

        ch_where, ch_params = self._build_ch_where_clause(filters)

        # ── ClickHouse primary path ──────────────────────────────────────────
        try:
            if period == 'yearly':
                trunc_fn = "toStartOfYear"
                label_fn = "toString(toYear(period_start))"
            elif period == 'quarterly':
                trunc_fn = "toStartOfQuarter"
                label_fn = "concat(toString(toYear(period_start)), '-Q', toString(toQuarter(period_start)))"
            else:  # monthly
                trunc_fn = "toStartOfMonth"
                label_fn = "formatDateTime(period_start, '%Y-%m')"

            rows_sql = _ch_q(f"""
                WITH base AS (
                    SELECT customer_mobile AS mob, parsed_date AS sale_d, invoice_number AS inv
                    FROM sales_data
                    WHERE {ch_where}
                      AND length(customer_mobile) = 10 AND customer_mobile != ''
                      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
                ),
                cust_first AS (
                    SELECT mob, {trunc_fn}(min(sale_d)) AS first_bucket
                    FROM base GROUP BY mob
                ),
                agg AS (
                    SELECT {trunc_fn}(b.sale_d) AS period_start,
                           COUNT(DISTINCT b.mob) AS total_members,
                           countDistinctIf(b.mob, {trunc_fn}(b.sale_d) = f.first_bucket) AS new_members,
                           COUNT(DISTINCT b.inv) AS total_visits
                    FROM base b JOIN cust_first f ON b.mob = f.mob
                    GROUP BY period_start
                )
                SELECT {label_fn} AS period_id, period_start, total_members, new_members, total_visits
                FROM agg ORDER BY period_start ASC
            """, ch_params)

            if rows_sql:
                db_start = 0
                if start_date:
                    first_start = rows_sql[0][1]
                    r0 = _ch_q1(f"""
                        WITH base AS (
                            SELECT customer_mobile AS mob, parsed_date AS sale_d
                            FROM sales_data
                            WHERE {ch_where}
                              AND length(customer_mobile) = 10 AND customer_mobile != ''
                              AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
                        ),
                        cust_first AS (
                            SELECT mob, {trunc_fn}(min(sale_d)) AS first_bucket FROM base GROUP BY mob
                        )
                        SELECT COUNT() FROM cust_first WHERE first_bucket < {{fs:Date}}
                    """, {**ch_params, 'fs': str(first_start)})
                    db_start = int(r0[0] or 0) if r0 else 0

                data, cumulative = [], db_start
                for i, row in enumerate(rows_sql):
                    pid = row[0]
                    total_m      = int(row[2] or 0)
                    new_m        = int(row[3] or 0)
                    total_visits = int(row[4] or 0)
                    repeat_m  = max(0, total_m - new_m)
                    eng_rate  = total_visits / total_m if total_m else 0
                    rep_pct   = repeat_m / total_m * 100 if total_m else 0

                    mom_tm = mom_v = mom_nm = mom_rm = 0.0
                    if i > 0:
                        prev = data[i - 1]
                        if prev['total_members']  > 0: mom_tm = (total_m - prev['total_members']) / prev['total_members'] * 100
                        if prev['total_visits']   > 0: mom_v  = (total_visits - prev['total_visits']) / prev['total_visits'] * 100
                        if prev['new_members']    > 0: mom_nm = (new_m - prev['new_members']) / prev['new_members'] * 100
                        if prev['repeat_members'] > 0: mom_rm = (repeat_m - prev['repeat_members']) / prev['repeat_members'] * 100
                    cumulative += new_m
                    data.append({
                        'month': pid,
                        'total_members': total_m,
                        'total_visits': total_visits,
                        'new_members': new_m,
                        'repeat_members': repeat_m,
                        'engagement_rate': round(float(eng_rate), 2),
                        'repeat_pct': round(float(rep_pct), 2),
                        'mom_total_members': round(mom_tm, 2),
                        'mom_visits': round(mom_v, 2),
                        'mom_new_members': round(mom_nm, 2),
                        'mom_repeat_members': round(mom_rm, 2),
                        'db_size': cumulative,
                    })

                cache.set(cache_key, (data, db_start), 86400)
                return data, db_start
        except Exception as e:
            print(f"[CH] retail_loyalty_matrix ClickHouse error: {e}")
            return {}

    # ── FY Loyalty Report ────────────────────────────────────────────────────
    def get_fy_loyalty_report(self, filters):
        """
        Financial Year Loyalty Report.
        Primary: ClickHouse. Fallback: raw PG.
        Results are cached.
        """
        import json
        import hashlib
        from django.core.cache import cache

        cache_key = "fy_loyalty2_" + hashlib.md5(json.dumps(filters, sort_keys=True).encode('utf-8')).hexdigest()
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        branch = filters.get('branch')
        has_branch = branch and str(branch).strip().lower() not in ('all branches', 'all', '')
        ch_where, ch_params = self._build_ch_where_clause(filters)

        def _build_fy_result(rows):
            result, cumulative_db, prev_cumulative_db = [], 0, 0
            for i, row in enumerate(rows):
                fy_year = int(row[0]); total_m = int(row[1] or 0); new_m = int(row[2] or 0)
                repeat_m = max(0, total_m - new_m)
                cumulative_db += new_m
                yoy_pct = round((total_m - result[i-1]['total_members'])/result[i-1]['total_members']*100, 2) if i > 0 and result[i-1]['total_members'] > 0 else None
                repeat_pct = round(repeat_m/total_m*100, 2) if total_m else 0
                retention_pct = round(repeat_m/prev_cumulative_db*100, 2) if prev_cumulative_db else 0
                prev_cumulative_db = cumulative_db
                result.append({
                    'fy_label': f'FY {fy_year}-{str(fy_year+1)[-2:]}', 'fy_year': fy_year,
                    'total_members': total_m, 'new_members': new_m, 'repeat_members': repeat_m,
                    'repeat_pct': repeat_pct, 'yoy_pct': yoy_pct,
                    'retention_pct_db': retention_pct, 'cumulative_db': cumulative_db,
                })
            return result

        # ── ClickHouse primary path ──────────────────────────────────────────
        try:
            rows = _ch_q(f"""
                WITH base AS (
                    SELECT customer_mobile AS mob, parsed_date AS sale_d
                    FROM sales_data
                    WHERE {ch_where}
                      AND length(customer_mobile) = 10 AND customer_mobile != ''
                      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
                ),
                cust_first AS (SELECT mob, min(sale_d) AS first_d FROM base GROUP BY mob),
                cust_fy AS (
                    SELECT b.mob,
                        if(toMonth(b.sale_d) >= 4, toYear(b.sale_d), toYear(b.sale_d) - 1) AS fy_year
                    FROM base b GROUP BY b.mob, fy_year
                )
                SELECT cfy.fy_year,
                       COUNT(DISTINCT cfy.mob) AS total_members,
                       countDistinctIf(cfy.mob,
                           if(toMonth(cf.first_d) >= 4, toYear(cf.first_d), toYear(cf.first_d) - 1) = cfy.fy_year
                       ) AS new_members
                FROM cust_fy cfy JOIN cust_first cf ON cf.mob = cfy.mob
                GROUP BY cfy.fy_year ORDER BY cfy.fy_year ASC
            """, ch_params)
            if rows:
                result = _build_fy_result(rows)
                cache.set(cache_key, result, 86400)
                return result
        except Exception as e:
            print(f"[CH] fy_loyalty_report ClickHouse error: {e}")
            return []

    # ── FY Sales Report ──────────────────────────────────────────────────────
    def get_fy_sales_report(self, filters):
        """
        Financial Year Sales Report.
        Primary: ClickHouse. Fallback: raw v_sales_data scan.
        """
        import json, hashlib
        from django.core.cache import cache

        cache_key = 'fy_sales_' + hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        branch     = str(filters.get('branch') or '').strip()
        staff      = filters.get('staff')
        rbm        = filters.get('rbm')
        bdm        = filters.get('bdm')
        start_date = _parse_date(filters.get('start_date'))
        end_date   = _parse_date(filters.get('end_date'))

        has_date   = bool(start_date or end_date)
        has_staff  = bool(staff or rbm or bdm)
        has_branch = bool(branch and branch.lower() not in ('all branches', 'all', ''))

        def _build_result(rows_iter):
            """Rows: (fy_year, total_sale, total_customers, new_sale)"""
            final_data, prev_total = [], None
            for row in rows_iter:
                fy_year        = int(row[0])
                total_sale     = float(row[1] or 0)
                total_customers = int(row[2] or 0)
                new_sale       = float(row[3] or 0)
                repeat_sale    = max(0.0, total_sale - new_sale)
                yoy_pct = round((total_sale - prev_total) / prev_total * 100, 2) if prev_total else None
                final_data.append({
                    'fy_label':             f'FY {fy_year}-{str(fy_year+1)[-2:]}',
                    'total_sale_cr':        round(total_sale / 10_000_000, 2),
                    'yoy_sale_pct':         yoy_pct,
                    'new_member_sale_cr':   round(new_sale / 10_000_000, 2),
                    'repeat_member_sale_cr': round(repeat_sale / 10_000_000, 2),
                    'repeat_sale_pct':      round(repeat_sale / total_sale * 100, 2) if total_sale else 0,
                    'asp':                  round(total_sale / total_customers, 2) if total_customers else 0,
                })
                prev_total = total_sale
            return final_data

        # ── ClickHouse primary path (all filter combinations) ───────────────
        ch_where, ch_params = self._build_ch_where_clause(filters)
        try:
            rows = _ch_q(f"""
                WITH base AS (
                    SELECT customer_mobile AS mob, parsed_date AS sale_d, total_value AS val
                    FROM sales_data
                    WHERE {ch_where}
                      AND length(customer_mobile) = 10 AND customer_mobile != ''
                      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
                ),
                cust_first AS (SELECT mob, min(sale_d) AS first_d FROM base GROUP BY mob),
                agg AS (
                    SELECT if(toMonth(b.sale_d) >= 4, toYear(b.sale_d), toYear(b.sale_d) - 1) AS fy_year,
                           SUM(b.val) AS total_sale,
                           COUNT(DISTINCT b.mob) AS total_customers,
                           sumIf(b.val,
                               if(toMonth(cf.first_d) >= 4, toYear(cf.first_d), toYear(cf.first_d) - 1)
                               = if(toMonth(b.sale_d) >= 4, toYear(b.sale_d), toYear(b.sale_d) - 1)
                           ) AS new_sale
                    FROM base b JOIN cust_first cf ON cf.mob = b.mob
                    GROUP BY fy_year
                )
                SELECT fy_year, total_sale, total_customers, new_sale FROM agg ORDER BY fy_year ASC
            """, ch_params)
            if rows:
                result = _build_result(rows)
                cache.set(cache_key, result, 86400)
                return result
        except Exception as e:
            print(f"[CH] fy_sales_report ClickHouse error: {e}")
            return []

    # ── Gap Analysis Base CTE (kept for backward compat) ─────────────────────
    def _get_gap_analysis_base_cte(self, where_sql):
        return f"""
            WITH raw_purchases AS (
                SELECT "Customer Mobile", "Invoice Number", "Branch", "Staff",
                    "Date" AS purchase_date, "Total Value"::FLOAT AS sales_value
                FROM {TABLE}
                WHERE {where_sql} AND "Customer Mobile" ~ '^[0-9]{{10}}$'
            ),
            customer_purchases AS (
                SELECT "Customer Mobile", MAX("Branch") AS "Branch", MAX("Staff") AS "Staff",
                    purchase_date, SUM(sales_value) AS daily_sales
                FROM raw_purchases GROUP BY "Customer Mobile", purchase_date
            ),
            ranked_purchases AS (
                SELECT *, LAG(purchase_date) OVER(PARTITION BY "Customer Mobile" ORDER BY purchase_date) AS prev_purchase_date
                FROM customer_purchases
            ),
            gap_data AS (
                SELECT *, (purchase_date - prev_purchase_date)::INT AS gap_days
                FROM ranked_purchases WHERE prev_purchase_date IS NOT NULL
            )
        """
