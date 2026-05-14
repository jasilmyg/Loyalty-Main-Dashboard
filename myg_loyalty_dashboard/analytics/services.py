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
    AND "Customer Mobile" ~ '^[0-9]{{10}}$'
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
            conditions.append(f'''(CASE
    WHEN SUBSTRING("Date"::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING("Date"::text, 1, 10), 'YYYY-MM-DD')
    WHEN SUBSTRING("Date"::text, 3, 1) = '-' THEN TO_DATE("Date"::text, 'DD-MM-YYYY')
    ELSE NULL
END) >= %s::DATE''')
            params.append(start_date)
        if end_date:
            conditions.append(f'''(CASE
    WHEN SUBSTRING("Date"::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING("Date"::text, 1, 10), 'YYYY-MM-DD')
    WHEN SUBSTRING("Date"::text, 3, 1) = '-' THEN TO_DATE("Date"::text, 'DD-MM-YYYY')
    ELSE NULL
END) <= %s::DATE''')
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

    # ── helpers kept for backward-compat ─────────────────────────────────────
    def _get_mobile_expr(self):   return '"Customer Mobile"'
    def _get_date_expr(self):     return '"Date"'
    def _get_val_expr(self):      return '"Total Value"'
    def _get_unique_customer_count(self, where_sql, params):
        r = _q1(f"""
            SELECT COUNT(DISTINCT "Customer Mobile")
            FROM {TABLE}
            WHERE {where_sql} AND {VALID_MOBILE}
        """, params)
        return r[0] or 0 if r else 0

    # ── Sales Overview ───────────────────────────────────────────────────────
    def get_sales_overview(self, filters):
        where_sql, params = self._build_where_clause(filters)

        # Use the materialized view (pre-aggregated monthly data) for speed.
        # We build a WHERE clause that maps to the mv columns.
        mv_conds, mv_params = [], []
        start_date = _parse_date(filters.get('start_date'))
        end_date   = _parse_date(filters.get('end_date'))
        branch     = filters.get('branch')
        staff      = filters.get('staff')
        rbm        = filters.get('rbm')
        bdm        = filters.get('bdm')

        if branch and str(branch).strip().lower() not in ('all branches', 'all', ''):
            mv_conds.append('UPPER("Branch") = UPPER(%s)'); mv_params.append(branch)
        if staff:
            mv_conds.append('UPPER("Staff") = UPPER(%s)'); mv_params.append(staff)
        if rbm:
            mv_conds.append('UPPER("RBM") = UPPER(%s)'); mv_params.append(rbm)
        if bdm:
            mv_conds.append('UPPER("BDM") = UPPER(%s)'); mv_params.append(bdm)
        if start_date:
            mv_conds.append('month_date >= %s::DATE'); mv_params.append(start_date)
        if end_date:
            mv_conds.append('month_date <= %s::DATE'); mv_params.append(end_date)

        mv_where = 'WHERE ' + ' AND '.join(mv_conds) if mv_conds else ''

        # Summary totals (sub-second from materialized view)
        row = _q1(f"""
            SELECT SUM(revenue)::FLOAT, SUM(invoices)
            FROM mv_monthly_summary
            {mv_where}
        """, mv_params)

        tr  = float(row[0] or 0) if row else 0
        ti  = int(row[1]   or 0) if row else 0
        atv = tr / ti if ti > 0 else 0

        # Monthly trend (aggregated from materialized view)
        monthly = _q(f"""
            SELECT
                TO_CHAR(month_date, 'Mon YY') AS m_label,
                SUM(revenue)::FLOAT           AS revenue
            FROM mv_monthly_summary
            {mv_where}
            GROUP BY month_date
            ORDER BY month_date ASC
        """, mv_params)

        return {
            'total_revenue':  tr,
            'total_invoices': ti,
            'atv':            atv,
            'monthly_trend':  [{'month': r[0], 'revenue': float(r[1] or 0)} for r in monthly],
        }


    # ── Customer Analytics ───────────────────────────────────────────────────
    def get_customer_analytics(self, filters):
        where_sql, params = self._build_where_clause(filters)

        if where_sql == "1=1":
            # Fast path using materialized view
            row = _q1("""
                SELECT 
                    SUM(total_spend) AS total_ltv,
                    COUNT(*) AS total_customers,
                    COUNT(CASE WHEN visits > 1 THEN 1 END) AS repeat_customers
                FROM mv_customer_summary
            """)
            if not row: row = (0, 0, 0)
            total_ltv = float(row[0] or 0)
            total_customers = int(row[1] or 0)
            repeat_customers = int(row[2] or 0)
        else:
            total_customers = (_q1(f"""
                SELECT COUNT(DISTINCT "Customer Mobile")
                FROM {TABLE} WHERE {where_sql} AND {VALID_MOBILE}
            """, params) or (0,))[0] or 0

            rows = _q(f"""
                WITH period_data AS (
                    SELECT "Customer Mobile" as mobile, SUM("Total Value")::FLOAT AS period_ltv
                    FROM {TABLE} WHERE {where_sql} AND {VALID_MOBILE}
                    GROUP BY "Customer Mobile"
                )
                SELECT SUM(p.period_ltv), COUNT(CASE WHEN m.visits > 1 THEN 1 END)
                FROM period_data p
                JOIN mv_customer_summary m ON p.mobile = m.mobile
            """, params)
            if not rows or not rows[0]:
                total_ltv, repeat_customers = 0, 0
            else:
                total_ltv = float(rows[0][0] or 0)
                repeat_customers = int(rows[0][1] or 0)

        repeat_rate = (repeat_customers / total_customers * 100) if total_customers > 0 else 0

        return {
            'total_ltv': total_ltv, 'total_customers': total_customers,
            'repeat_customers': repeat_customers, 'repeat_purchase_rate': repeat_rate,
        }

    # ── Frequency Distribution ───────────────────────────────────────────────
    def get_frequency_distribution(self, filters):
        where_sql, params = self._build_where_clause(filters)

        if where_sql == "1=1":
            stats_query = """
                SELECT mobile AS "Customer Mobile", visits, total_spend AS revenue
                FROM mv_customer_summary
            """
            params = []
        else:
            stats_query = f"""
                SELECT "Customer Mobile",
                    COUNT(DISTINCT "Date")    AS visits,
                    SUM("Total Value")::FLOAT AS revenue
                FROM {TABLE} WHERE {where_sql} AND {VALID_MOBILE}
                GROUP BY "Customer Mobile"
            """

        rows = _q(f"""
            WITH customer_stats AS (
                {stats_query}
            ),
            bucketed AS (
                SELECT
                    CASE
                        WHEN visits = 1                THEN '1 Visit'
                        WHEN visits = 2                THEN '2 Visits'
                        WHEN visits = 3                THEN '3 Visits'
                        WHEN visits = 4                THEN '4 Visits'
                        WHEN visits BETWEEN 5  AND 9   THEN '5-9 Visits'
                        WHEN visits BETWEEN 10 AND 20  THEN '10-20 Visits'
                        WHEN visits BETWEEN 21 AND 50  THEN '21-50 Visits'
                        WHEN visits BETWEEN 51 AND 100 THEN '51-100 Visits'
                        ELSE 'Above 100 Visits'
                    END AS segment,
                    visits, revenue
                FROM customer_stats
            )
            SELECT segment,
                COUNT(*) AS customers,
                COALESCE(SUM(revenue),0) AS net_revenue,
                COUNT(*)*100.0/SUM(COUNT(*)) OVER() AS cust_pct,
                COALESCE(SUM(revenue),0)*100.0/NULLIF(SUM(SUM(revenue)) OVER(),0) AS rev_pct,
                COALESCE(SUM(revenue),0)/NULLIF(COUNT(*),0) AS asp
            FROM bucketed GROUP BY segment
            ORDER BY CASE segment
                WHEN '1 Visit' THEN 1 WHEN '2 Visits' THEN 2 WHEN '3 Visits' THEN 3
                WHEN '4 Visits' THEN 4 WHEN '5-9 Visits' THEN 5 WHEN '10-20 Visits' THEN 6
                WHEN '21-50 Visits' THEN 7 WHEN '51-100 Visits' THEN 8 ELSE 9 END
        """, params)

        return [{'segment': r[0], 'customers': r[1],
                 'net_revenue': round(float(r[2] or 0), 2),
                 'cust_pct':    round(float(r[3] or 0), 2),
                 'rev_pct':     round(float(r[4] or 0), 2),
                 'asp':         round(float(r[5] or 0), 2)} for r in rows]

    SEGMENT_CHUNK_SIZE = 1_000_000
    _SEGMENT_FILTER = {
        '1 Visit':'visits=1', '2 Visits':'visits=2', '3 Visits':'visits=3',
        '4 Visits':'visits=4', '5-9 Visits':'visits BETWEEN 5 AND 9',
        '10-20 Visits':'visits BETWEEN 10 AND 20', '21-50 Visits':'visits BETWEEN 21 AND 50',
        '51-100 Visits':'visits BETWEEN 51 AND 100', 'Above 100 Visits':'visits>100',
    }

    def get_customers_for_segment(self, filters, segment, offset=0):
        where_sql, params = self._build_where_clause(filters)
        seg_pred = self._SEGMENT_FILTER.get(segment, '1=0')
        with connection.cursor() as cur:
            cur.execute(f"""
                WITH customer_stats AS (
                    SELECT "Customer Mobile", MAX("Customer Name") AS customer_name,
                        COUNT(DISTINCT "Date") AS visits,
                        SUM("Total Value")::FLOAT AS net_revenue,
                        MAX("Date") AS last_visit
                    FROM {TABLE} WHERE {where_sql} AND {VALID_MOBILE}
                    GROUP BY "Customer Mobile"
                )
                SELECT "Customer Mobile", customer_name, visits, net_revenue, last_visit
                FROM customer_stats WHERE {seg_pred}
                ORDER BY net_revenue DESC NULLS LAST
                LIMIT {self.SEGMENT_CHUNK_SIZE} OFFSET {offset}
            """, params)
            return [d[0] for d in cur.description], cur.fetchall()

    def count_customers_for_segment(self, filters, segment):
        where_sql, params = self._build_where_clause(filters)
        seg_pred = self._SEGMENT_FILTER.get(segment, '1=0')
        r = _q1(f"""
            WITH cs AS (
                SELECT "Customer Mobile", COUNT(DISTINCT "Date") AS visits
                FROM {TABLE} WHERE {where_sql} AND {VALID_MOBILE}
                GROUP BY "Customer Mobile"
            )
            SELECT COUNT(*) FROM cs WHERE {seg_pred}
        """, params)
        return r[0] if r else 0

    # ── RFM ──────────────────────────────────────────────────────────────────
    def _get_rfm_base_cte(self, where_sql, params=None):
        if where_sql == "1=1":
            # Fast path: use pre-computed mv_customer_summary (5M rows, indexed)
            return """
                WITH rfm_base AS (
                    SELECT cd.mobile,
                        '' AS customer_name,
                        (CURRENT_DATE - cd.lv_month)::INT AS recency,
                        cs.visits AS frequency,
                        cs.total_spend AS monetary,
                        cd.lv_month AS last_visit
                    FROM mv_customer_dates cd
                    JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
                    WHERE cs.total_spend IS NOT NULL
                      AND cd.lv_month >= cd.fv_month
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
        where_sql, params = self._build_where_clause(filters)

        # ── Lightning-fast path: mv_rfm_summary (6 pre-aggregated rows, ~40ms) ──
        if where_sql == '1=1':
            from django.core.cache import cache
            _ck = 'rfm_segments_global'
            _cached = cache.get(_ck)
            if _cached is not None:
                return _cached
            try:
                rows = _q("SELECT segment, customer_count, total_revenue, avg_revenue FROM mv_rfm_summary ORDER BY customer_count DESC")
                if rows:
                    result = [{'segment': r[0], 'count': r[1],
                               'total_revenue': float(r[2] or 0), 'avg_revenue': float(r[3] or 0)}
                              for r in rows]
                    cache.set(_ck, result, 86400)  # 24 h
                    return result
            except Exception:
                pass  # mv_rfm_summary not ready, fall through to row-level MV

            # Fallback: GROUP BY on mv_rfm_segments (still 4.2M rows but pre-computed)
            try:
                rows = _q("""
                    SELECT segment, COUNT(mobile)::bigint AS count,
                           SUM(monetary)::FLOAT AS total_revenue, AVG(monetary)::FLOAT AS avg_revenue
                    FROM mv_rfm_segments
                    GROUP BY segment ORDER BY count DESC
                """)
                if rows:
                    result = [{'segment': r[0], 'count': r[1],
                               'total_revenue': float(r[2] or 0), 'avg_revenue': float(r[3] or 0)}
                              for r in rows]
                    cache.set(_ck, result, 86400)
                    return result
            except Exception:
                pass  # MV not ready, fall through to raw scan

        # ── Slow path: raw scan with filters ────────────────────────────────
        cte = self._get_rfm_base_cte(where_sql)
        rows = _q(f"""
            {cte}
            SELECT segment, COUNT(mobile) AS count,
                SUM(monetary)::FLOAT AS total_revenue, AVG(monetary)::FLOAT AS avg_revenue
            FROM segmented GROUP BY segment ORDER BY count DESC
        """, params)
        return [{'segment': r[0], 'count': r[1],
                 'total_revenue': float(r[2] or 0), 'avg_revenue': float(r[3] or 0)} for r in rows]

    def perform_rfm_analysis(self, filters):
        return self.get_rfm_segments(filters)

    def get_monetary_quintiles(self, filters):
        where_sql, params = self._build_where_clause(filters)
        labels = {1:'Top 20%', 2:'Next 20%', 3:'Middle 20%', 4:'Next 20%', 5:'Bottom 20%'}

        # ── Lightning-fast path: mv_monetary_quintiles (5 rows, ~40ms) ────────
        if where_sql == '1=1':
            from django.core.cache import cache
            _ck = 'monetary_quintiles_global'
            _cached = cache.get(_ck)
            if _cached is not None:
                return _cached
            try:
                rows = _q("SELECT quintile, avg_spend, customer_count FROM mv_monetary_quintiles ORDER BY quintile")
                if rows:
                    result = [{'label': labels.get(r[0], f'Group {r[0]}'),
                               'avg_spend': float(r[1] or 0), 'count': r[2]}
                              for r in rows]
                    cache.set(_ck, result, 86400)  # 24 h
                    return result
            except Exception:
                pass  # MV not ready, fall through

            # Fallback: NTILE over mv_customer_summary
            cs_query = "SELECT mobile, total_spend FROM mv_customer_summary"
            params = []
        else:
            cs_query = f"""
                SELECT "Customer Mobile", SUM("Total Value")::FLOAT AS total_spend
                FROM {TABLE} WHERE {where_sql} AND {VALID_MOBILE}
                GROUP BY "Customer Mobile"
            """

        rows = _q(f"""
            WITH cs AS ({cs_query}),
            sc AS (SELECT total_spend, NTILE(5) OVER(ORDER BY total_spend DESC) AS quintile FROM cs)
            SELECT quintile, AVG(total_spend)::FLOAT, COUNT(*) FROM sc GROUP BY quintile ORDER BY quintile
        """, params)
        return [{'label': labels.get(r[0], f'Group {r[0]}'), 'avg_spend': float(r[1] or 0), 'count': r[2]} for r in rows]

    def get_rfm_details_query(self, filters, segment=None):
        where_sql, params = self._build_where_clause(filters)
        cte = self._get_rfm_base_cte(where_sql)
        where_seg = ''
        if segment:
            where_seg = 'WHERE segment = %s'
            params = list(params) + [segment]
        query = f"""
            {cte}
            SELECT customer_name AS "Customer Name", mobile AS "Customer Mobile",
                recency AS "Recency (Days)", frequency AS "Frequency (Visits)",
                monetary AS "Monetary Value", r_score AS "R Score",
                f_score AS "F Score", m_score AS "M Score",
                rfm_code AS "RFM Code", segment AS "RFM Segment",
                last_visit AS "Last Visit Date"
            FROM segmented {where_seg}
            ORDER BY monetary DESC NULLS LAST
        """
        return query, params

    # ── Cohort Retention ─────────────────────────────────────────────────────
    def get_cohort_retention(self):
        from django.core.cache import cache
        _ck = 'cohort_retention_global'
        _cached = cache.get(_ck)
        if _cached is not None:
            return _cached

        # Fast path: use pre-computed MV if available
        try:
            rows = _q("SELECT cohort_month, month_number, num_users FROM mv_cohort_retention ORDER BY cohort_month, month_number")
            if rows:
                cohorts = {}
                for row in rows:
                    c_month, m_num, count = row
                    if c_month not in cohorts:
                        cohorts[c_month] = {}
                    cohorts[c_month][m_num] = count
                result = {'cohorts': cohorts}
                cache.set(_ck, result, 86400)
                return result
        except Exception:
            pass  # MV not ready, fall through

        # Slow path: raw scan (only runs once; result is cached 24h)
        rows = _q(f"""
            WITH cohort_items AS (
                SELECT "Customer Mobile",
                    TO_CHAR(MIN("Date"), 'YYYY-MM') AS cohort_month
                FROM {TABLE}
                WHERE "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
                GROUP BY "Customer Mobile"
            ),
            user_activities AS (
                SELECT a."Customer Mobile",
                    (
                      (EXTRACT(YEAR FROM a."Date")::INT * 12 + EXTRACT(MONTH FROM a."Date")::INT)
                      - (EXTRACT(YEAR FROM (c.cohort_month||'-01')::DATE)::INT * 12
                         + EXTRACT(MONTH FROM (c.cohort_month||'-01')::DATE)::INT)
                    ) AS month_number,
                    c.cohort_month
                FROM {TABLE} a
                JOIN cohort_items c ON a."Customer Mobile" = c."Customer Mobile"
                WHERE a."Customer Mobile" IS NOT NULL AND a."Customer Mobile" != ''
            )
            SELECT cohort_month, month_number, COUNT(DISTINCT "Customer Mobile") AS num_users
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
        cache.set(_ck, result, 86400)  # cache 24 h
        return result

    def get_yearly_cohort_analysis(self):
        from django.core.cache import cache
        _ck = 'yearly_cohort_global'
        _cached = cache.get(_ck)
        if _cached is not None:
            return _cached

        # Fast path: use pre-computed MV if available
        try:
            rows = _q("""
                SELECT cohort_year, year_index, active_customers, year_revenue,
                       initial_size, retention_rate, one_time_buyers, no_return_purchases
                FROM mv_yearly_cohort
                ORDER BY cohort_year DESC, year_index ASC
            """)
            if rows:
                rfm_rows = _q("SELECT cohort_year, segment, customer_count FROM mv_cohort_rfm ORDER BY cohort_year")
                cohort_data = {}
                for r in rows:
                    cy, yi, active, rev, size, rate, otb, nrp = r
                    size = int(size or 0)
                    if cy not in cohort_data:
                        cohort_data[cy] = {
                            'size': size, 'one_time_buyers': int(otb or 0),
                            'otb_pct': round(float(otb or 0)*100/size, 2) if size else 0,
                            'no_return_purchases': int(nrp or 0),
                            'nrp_pct': round(float(nrp or 0)*100/size, 2) if size else 0,
                            'years': {},
                        }
                    cohort_data[cy]['years'][int(yi)] = {
                        'active': int(active or 0), 'revenue': round(float(rev or 0), 2),
                        'retention': round(float(rate or 0), 2),
                        'ltv': round(float(rev or 0)/size, 2) if size else 0,
                    }
                for rr in rfm_rows:
                    cy, seg, count = rr
                    if cy in cohort_data:
                        if 'rfm' not in cohort_data[cy]:
                            cohort_data[cy]['rfm'] = {}
                        cohort_data[cy]['rfm'][seg] = count
                cache.set(_ck, cohort_data, 86400)
                return cohort_data
        except Exception:
            pass  # MVs not ready, fall through

        # Slow path: raw scan (only runs once; result is cached 24h)
        rows = _q(f"""
            WITH customer_first_visit AS (
                SELECT "Customer Mobile" AS mobile, MIN("Date") AS first_date
                FROM {TABLE}
                WHERE "Customer Mobile" ~ '^[0-9]{{10}}$'
                GROUP BY "Customer Mobile"
            ),
            customer_activities AS (
                SELECT s."Customer Mobile" AS mobile, s."Date" AS activity_date,
                    s."Total Value"::FLOAT AS revenue, f.first_date,
                    TO_CHAR(f.first_date, 'YYYY') AS cohort_year,
                    (EXTRACT(YEAR FROM s."Date")::INT - EXTRACT(YEAR FROM f.first_date)::INT) AS year_index
                FROM {TABLE} s
                JOIN customer_first_visit f ON s."Customer Mobile" = f.mobile
            ),
            cohort_yearly_stats AS (
                SELECT cohort_year, year_index,
                    COUNT(DISTINCT mobile) AS active_customers,
                    SUM(revenue)::FLOAT    AS year_revenue
                FROM customer_activities GROUP BY cohort_year, year_index
            ),
            cohort_base_size AS (
                SELECT cohort_year, active_customers AS initial_size
                FROM cohort_yearly_stats WHERE year_index = 0
            ),
            cohort_otb AS (
                SELECT cohort_year, COUNT(DISTINCT mobile) AS one_time_buyers
                FROM (
                    SELECT mobile, cohort_year, COUNT(DISTINCT activity_date) AS lv
                    FROM customer_activities GROUP BY mobile, cohort_year
                ) t WHERE lv = 1 GROUP BY cohort_year
            ),
            cohort_nrp AS (
                SELECT cohort_year, COUNT(DISTINCT mobile) AS no_return_purchases
                FROM (
                    SELECT mobile, cohort_year, MAX(year_index) AS myi
                    FROM customer_activities GROUP BY mobile, cohort_year
                ) t WHERE myi = 0 GROUP BY cohort_year
            )
            SELECT s.cohort_year, s.year_index, s.active_customers, s.year_revenue,
                b.initial_size,
                (s.active_customers * 100.0 / NULLIF(b.initial_size, 0)) AS retention_rate,
                COALESCE(o.one_time_buyers, 0), COALESCE(n.no_return_purchases, 0)
            FROM cohort_yearly_stats s
            JOIN cohort_base_size b ON s.cohort_year = b.cohort_year
            LEFT JOIN cohort_otb o ON s.cohort_year = o.cohort_year
            LEFT JOIN cohort_nrp n ON s.cohort_year = n.cohort_year
            ORDER BY s.cohort_year DESC, s.year_index ASC
        """)
        cohort_data = {}
        for r in rows:
            cy, yi, active, rev, size, rate, otb, nrp = r
            size = int(size or 0)
            if cy not in cohort_data:
                cohort_data[cy] = {
                    'size': size, 'one_time_buyers': int(otb or 0),
                    'otb_pct': round(float(otb or 0)*100/size, 2) if size else 0,
                    'no_return_purchases': int(nrp or 0),
                    'nrp_pct': round(float(nrp or 0)*100/size, 2) if size else 0,
                    'years': {},
                }
            cohort_data[cy]['years'][int(yi)] = {
                'active': int(active or 0), 'revenue': round(float(rev or 0), 2),
                'retention': round(float(rate or 0), 2),
                'ltv': round(float(rev or 0)/size, 2) if size else 0,
            }
        rfm_rows = _q(f"""
            WITH cfv AS (
                SELECT "Customer Mobile" AS mobile, MIN("Date") AS first_date
                FROM {TABLE} WHERE "Customer Mobile" ~ '^[0-9]{{10}}$'
                GROUP BY "Customer Mobile"
            ),
            rfm_stats AS (
                SELECT TO_CHAR(f.first_date, 'YYYY') AS c_year,
                    CASE
                        WHEN (CURRENT_DATE-MAX(s."Date"))<=90  AND COUNT(DISTINCT s."Date")>=3 THEN 'Champions'
                        WHEN (CURRENT_DATE-MAX(s."Date"))<=180 AND COUNT(DISTINCT s."Date")>=2 THEN 'Loyal'
                        WHEN (CURRENT_DATE-MAX(s."Date"))>365                                  THEN 'Lost'
                        ELSE 'Average'
                    END AS segment
                FROM {TABLE} s JOIN cfv f ON s."Customer Mobile" = f.mobile
                GROUP BY f.mobile, TO_CHAR(f.first_date, 'YYYY')
            )
            SELECT c_year, segment, COUNT(*) FROM rfm_stats GROUP BY c_year, segment
        """)
        for rr in rfm_rows:
            cy, seg, count = rr
            if cy in cohort_data:
                if 'rfm' not in cohort_data[cy]:
                    cohort_data[cy]['rfm'] = {}
                cohort_data[cy]['rfm'][seg] = count
        cache.set(_ck, cohort_data, 86400)  # cache 24 h
        return cohort_data

    # ── Payment & Discount (legacy columns not in view – return empty) ────────
    def get_payment_analytics(self, filters):    return {'distribution': {}}
    def get_discount_analysis(self, filters):   return {'distribution': {}}

    # ── Staff Performance ────────────────────────────────────────────────────
    def get_staff_performance(self, filters):
        where_sql, params = self._build_where_clause(filters)
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

        # ── Fast path: use pre-computed MV for global (no-filter) view ──────
        if where_sql == '1=1':
            try:
                rows = _q("""
                    SELECT gap_range, customers,
                           customers*100.0/SUM(customers) OVER() AS pct,
                           avg_gap, sort_order
                    FROM mv_gap_analysis ORDER BY sort_order ASC
                """)
                if rows:
                    return [{
                        'segment': r[0], 'count': r[1],
                        'percentage': round(float(r[2] or 0), 2),
                        'avg_gap': round(float(r[3] or 0), 1),
                        'signal': signals.get(r[4], ('Medium','Medium',''))[0],
                        'priority': signals.get(r[4], ('Medium','Medium',''))[1],
                        'action': signals.get(r[4], ('Medium','Medium',''))[2],
                    } for r in rows]
            except Exception:
                pass  # MV not ready, fall through

        # ── Slow path: raw scan with filters ────────────────────────────────
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
        return [{
            'segment': r[0], 'count': r[1],
            'percentage': round(float(r[2] or 0), 2),
            'avg_gap': round(float(r[3] or 0), 1),
            'signal': signals.get(r[4], ('Medium','Medium',''))[0],
            'priority': signals.get(r[4], ('Medium','Medium',''))[1],
            'action': signals.get(r[4], ('Medium','Medium',''))[2],
        } for r in rows]

    def get_customer_segmentation_matrix(self, filters):
        where_sql, params = self._build_where_clause(filters)
        
        if where_sql == "1=1":
            cs_query = """
                SELECT mobile, visits, total_spend, (CURRENT_DATE - (CASE
    WHEN SUBSTRING(last_visit::text, 5, 1) = '-' THEN TO_DATE(SUBSTRING(last_visit::text, 1, 10), 'YYYY-MM-DD')
    WHEN SUBSTRING(last_visit::text, 3, 1) = '-' THEN TO_DATE(last_visit::text, 'DD-MM-YYYY')
    ELSE NULL
END))::INT AS recency_days
                FROM mv_customer_summary
            """
            params = []
        else:
            cs_query = f"""
                SELECT "Customer Mobile",
                    COUNT(DISTINCT "Date") AS visits,
                    SUM("Total Value")::FLOAT AS total_spend,
                    (CURRENT_DATE - MAX("Date"))::INT AS recency_days
                FROM {TABLE}
                WHERE {where_sql} AND "Customer Mobile" ~ '^[0-9]{{10}}$'
                GROUP BY "Customer Mobile"
            """
            
        rows = _q(f"""
            WITH cs AS (
                {cs_query}
            )
            SELECT
                CASE WHEN visits=1 THEN 'One-Time' WHEN visits<=3 THEN 'Occasional' ELSE 'Frequent' END AS freq_seg,
                CASE WHEN recency_days<=90 THEN 'Active' WHEN recency_days<=365 THEN 'Lapsing' ELSE 'Inactive' END AS rec_seg,
                COUNT(*) AS customers, AVG(total_spend)::FLOAT AS avg_spend
            FROM cs GROUP BY freq_seg, rec_seg ORDER BY freq_seg, rec_seg
        """, params)
        return [{'freq':r[0],'recency':r[1],'customers':r[2],'avg_spend':round(float(r[3] or 0),2)} for r in rows]

    def get_action_engine_data(self, filters):
        where_sql, params = self._build_where_clause(filters)

        # ── Lightning fast path: global queries (no filter) ─────────────────
        if where_sql == '1=1':
            from django.core.cache import cache
            _ck = 'action_engine_global'
            _cached = cache.get(_ck)
            if _cached is not None:
                return _cached

            # Tier 1: mv_action_engine (3 pre-computed rows, ~37ms)
            try:
                rows = _q("SELECT segment, customers, revenue_at_risk, action FROM mv_action_engine WHERE customers > 0")
                if rows:
                    result = [{'segment': r[0], 'customers': r[1],
                               'revenue_at_risk': round(float(r[2] or 0), 2), 'action': r[3]}
                              for r in rows]
                    cache.set(_ck, result, 86400)
                    return result
            except Exception:
                pass  # MV not ready, fall through

            # Tier 2: GREATEST/LEAST over mv_customer_summary (~39s fallback)
            _DATE_EXPR = """
                CASE
                    WHEN {col} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                        THEN SUBSTRING({col}, 1, 10)::DATE
                    WHEN {col} ~ '^[0-9]{{2}}-[0-9]{{2}}-[0-9]{{4}}'
                        THEN TO_DATE({col}, 'DD-MM-YYYY')
                    ELSE NULL
                END
            """
            last_expr  = _DATE_EXPR.format(col='last_visit')
            first_expr = _DATE_EXPR.format(col='first_visit')
            try:
                rows = _q(f"""
                    WITH cs AS (
                        SELECT mobile, visits, total_spend,
                            (CURRENT_DATE - GREATEST({last_expr}, {first_expr}))::INT AS recency_days
                        FROM mv_customer_summary
                    )
                    SELECT 'Lapsing High Value', COUNT(*), SUM(total_spend)::FLOAT,
                           'Send Win-Back SMS with custom discount'
                    FROM cs WHERE recency_days BETWEEN 90 AND 180 AND total_spend >= 10000
                    UNION ALL
                    SELECT 'Recently Active', COUNT(*), SUM(total_spend)::FLOAT,
                           'Nurture with product feedback loop'
                    FROM cs WHERE recency_days <= 30 AND visits = 1
                    UNION ALL
                    SELECT 'Frequent Shoppers at Risk', COUNT(*), SUM(total_spend)::FLOAT,
                           'Trigger premium loyalty offer'
                    FROM cs WHERE recency_days BETWEEN 45 AND 90 AND visits >= 3
                """)
                result = [{'segment': r[0], 'customers': r[1],
                           'revenue_at_risk': round(float(r[2] or 0), 2), 'action': r[3]}
                          for r in rows if r[1] > 0]
                cache.set(_ck, result, 86400)
                return result
            except Exception:
                pass  # Fall through to raw scan

        # ── Slow path: raw scan with applied filters ─────────────────────────
        rows = _q(f"""
            WITH cs AS (
                SELECT "Customer Mobile",
                    COUNT(DISTINCT "Date") AS visits,
                    SUM("Total Value")::FLOAT AS total_spend,
                    (CURRENT_DATE - MAX("Date"))::INT AS recency_days
                FROM {TABLE}
                WHERE {where_sql} AND "Customer Mobile" ~ '^[0-9]{{10}}$'
                GROUP BY "Customer Mobile"
            )
            SELECT 'Lapsing High Value', COUNT(*), SUM(total_spend)::FLOAT, 'Send Win-Back SMS with custom discount'
            FROM cs WHERE recency_days BETWEEN 90 AND 180 AND total_spend >= 10000
            UNION ALL
            SELECT 'Recently Active', COUNT(*), SUM(total_spend)::FLOAT, 'Nurture with product feedback loop'
            FROM cs WHERE recency_days <= 30 AND visits = 1
            UNION ALL
            SELECT 'Frequent Shoppers at Risk', COUNT(*), SUM(total_spend)::FLOAT, 'Trigger premium loyalty offer'
            FROM cs WHERE recency_days BETWEEN 45 AND 90 AND visits >= 3
        """, params)
        return [{'segment':r[0],'customers':r[1],'revenue_at_risk':round(float(r[2] or 0),2),'action':r[3]} for r in rows if r[1]>0]

    def get_business_insights(self, filters):        return []
    def get_cohort_business_insights(self):        return []

    # ── Loyalty KPIs ─────────────────────────────────────────────────────────
    def get_loyalty_overview_kpis(self, filters):
        where_sql, params = self._build_where_clause(filters)

        if where_sql == '1=1':
            from django.core.cache import cache
            _ck = 'loyalty_kpi_global'
            _cached = cache.get(_ck)
            if _cached is not None:
                return _cached

            # ── Tier 1: mv_loyalty_kpis (1-row pre-computed MV, ~37ms) ────────
            try:
                row = _q1("""
                    SELECT total_customers, repeat_customers, avg_gap_days
                    FROM mv_loyalty_kpis
                """)
                if row and row[0]:
                    total, repeat = int(row[0]), int(row[1])
                    result = {
                        'total_customers':  total,
                        'repeat_customers': repeat,
                        'repeat_rate':      round(repeat / total * 100, 1) if total else 0,
                        'avg_gap':          round(float(row[2] or 0), 1),
                    }
                    cache.set(_ck, result, 86400)
                    return result
            except Exception:
                pass  # MV not ready, fall through

            # ── Tier 2: GREATEST/LEAST over mv_customer_summary (slow, ~54s) ──
            _PARSE = (
                "CASE "
                "  WHEN {col} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
                "      THEN SUBSTRING({col}, 1, 10)::DATE "
                "  WHEN {col} ~ '^[0-9]{{2}}-[0-9]{{2}}-[0-9]{{4}}' "
                "      THEN TO_DATE({col}, 'DD-MM-YYYY') "
                "  ELSE NULL "
                "END"
            )
            last_expr  = _PARSE.format(col='last_visit')
            first_expr = _PARSE.format(col='first_visit')
            row = _q1(f"""
                WITH parsed AS (
                    SELECT mobile, visits,
                        GREATEST({last_expr}, {first_expr}) AS d_last,
                        LEAST({last_expr},    {first_expr}) AS d_first
                    FROM mv_customer_summary
                )
                SELECT
                    COUNT(mobile),
                    COUNT(mobile) FILTER (WHERE visits > 1),
                    AVG(
                        CASE WHEN visits > 1 AND d_last IS NOT NULL AND d_first IS NOT NULL
                             THEN (d_last - d_first)::FLOAT / (visits - 1)
                             ELSE NULL
                        END
                    )::FLOAT
                FROM parsed
            """)
        else:
            # ── Filtered path: LAG-based exact avg gap on v_sales_data ───────
            row = _q1(f"""
                WITH cs AS (
                    SELECT "Customer Mobile",
                        COUNT(DISTINCT "Date") AS visits
                    FROM {TABLE}
                    WHERE {where_sql} AND "Customer Mobile" ~ '^[0-9]{{10}}$'
                    GROUP BY "Customer Mobile"
                ),
                gap_data AS (
                    SELECT s."Customer Mobile",
                        (s."Date" - LAG(s."Date") OVER (
                            PARTITION BY s."Customer Mobile" ORDER BY s."Date"
                        ))::INT AS gap_days
                    FROM {TABLE} s
                    WHERE {where_sql} AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                ),
                customer_avg_gaps AS (
                    SELECT "Customer Mobile",
                        AVG(gap_days)::FLOAT AS avg_gap_days
                    FROM gap_data WHERE gap_days IS NOT NULL AND gap_days > 0
                    GROUP BY "Customer Mobile"
                )
                SELECT
                    COUNT(DISTINCT c."Customer Mobile"),
                    COUNT(DISTINCT CASE WHEN c.visits > 1 THEN c."Customer Mobile" END),
                    AVG(g.avg_gap_days)::FLOAT
                FROM cs c
                LEFT JOIN customer_avg_gaps g ON c."Customer Mobile" = g."Customer Mobile"
            """, params + params)

        if not row:
            return {'total_customers': 0, 'repeat_customers': 0, 'repeat_rate': 0, 'avg_gap': 0}
        total, repeat = int(row[0] or 0), int(row[1] or 0)
        result = {
            'total_customers':  total,
            'repeat_customers': repeat,
            'repeat_rate':      round(repeat / total * 100, 1) if total else 0,
            'avg_gap':          round(float(row[2] or 0), 1),
        }
        if where_sql == '1=1':
            from django.core.cache import cache
            cache.set('loyalty_kpi_global', result, 86400)
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
        Uses pre-computed materialized views (mv_monthly_members / mv_monthly_members_branch)
        for instant sub-second responses. Falls back to raw scan if MVs not yet available.
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

        # ── Fast path: use pre-computed MVs ────────────────────────────────
        if not has_dim_filter or has_branch:
            if period == 'monthly':
                trunc_expr = 'month_date'
                label_expr = "TO_CHAR(month_date, 'YYYY-MM')"
            elif period == 'yearly':
                trunc_expr = "DATE_TRUNC('year', month_date)::date"
                label_expr = "TO_CHAR(DATE_TRUNC('year', month_date), 'YYYY')"
            else:  # quarterly
                trunc_expr = "DATE_TRUNC('quarter', month_date)::date"
                label_expr = "TO_CHAR(DATE_TRUNC('quarter', month_date), 'YYYY')||'-Q'||EXTRACT(QUARTER FROM month_date)::TEXT"

            period_filter, period_params = [], []
            if start_date:
                period_filter.append(f'{trunc_expr} >= %s::DATE')
                period_params.append(start_date)
            if end_date:
                period_filter.append(f'{trunc_expr} <= %s::DATE')
                period_params.append(end_date)
            pf = (' AND ' + ' AND '.join(period_filter)) if period_filter else ''

            if has_branch:
                # branch-level MV
                mv_sql = f"""
                    SELECT {label_expr} AS period_id,
                           {trunc_expr} AS period_start,
                           SUM(total_members)::bigint,
                           SUM(new_members)::bigint,
                           SUM(total_visits)::bigint
                    FROM mv_monthly_members_branch
                    WHERE UPPER(branch) = UPPER(%s){pf}
                    GROUP BY 1, 2 ORDER BY 2 ASC
                """
                params = [branch] + period_params
            else:
                # global MV
                mv_sql = f"""
                    SELECT {label_expr} AS period_id,
                           {trunc_expr} AS period_start,
                           SUM(total_members)::bigint,
                           SUM(new_members)::bigint,
                           SUM(total_visits)::bigint
                    FROM mv_monthly_members
                    WHERE 1=1{pf}
                    GROUP BY 1, 2 ORDER BY 2 ASC
                """
                params = period_params

            try:
                rows_sql = _q(mv_sql, params)
                if rows_sql:  # MVs are available and have data
                    # cumulative DB size before start date
                    db_start = 0
                    if rows_sql and start_date:
                        first_start = rows_sql[0][1]
                        if has_branch:
                            r0 = _q1("""
                                SELECT SUM(new_members)::bigint FROM mv_monthly_members_branch
                                WHERE UPPER(branch) = UPPER(%s) AND month_date < %s::DATE
                            """, [branch, first_start])
                        else:
                            r0 = _q1("""
                                SELECT SUM(new_members)::bigint FROM mv_monthly_members
                                WHERE month_date < %s::DATE
                            """, [first_start])
                        db_start = int(r0[0] or 0) if r0 else 0

                    data, cumulative = [], db_start
                    for i, row in enumerate(rows_sql):
                        pid = row[0]
                        total_m   = int(row[2] or 0)
                        new_m     = int(row[3] or 0)
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
            except Exception:
                pass  # MV not yet created — fall through to raw scan

        # ── Slow fallback: raw scan (only for staff/rbm/bdm filters until branch MVs extended) ──
        dim_sql_parts, dim_params = [], []
        if has_branch:
            dim_sql_parts.append('UPPER(s."Branch") = UPPER(%s)'); dim_params.append(branch)
        if staff:
            dim_sql_parts.append('UPPER(s."Staff") = UPPER(%s)'); dim_params.append(staff)
        if rbm:
            dim_sql_parts.append('UPPER(s."RBM") = UPPER(%s)'); dim_params.append(rbm)
        if bdm:
            dim_sql_parts.append('UPPER(s."BDM") = UPPER(%s)'); dim_params.append(bdm)
        dim_sql = (' AND ' + ' AND '.join(dim_sql_parts)) if dim_sql_parts else ''

        if period == 'yearly':
            trunc_act   = "DATE_TRUNC('year',  b.sale_d)::date"
            trunc_first = "DATE_TRUNC('year',  MIN(b.sale_d))::date"
            period_label = "TO_CHAR(DATE_TRUNC('year', b.sale_d), 'YYYY')"
        elif period == 'quarterly':
            trunc_act   = "DATE_TRUNC('quarter', b.sale_d)::date"
            trunc_first = "DATE_TRUNC('quarter', MIN(b.sale_d))::date"
            period_label = "TO_CHAR(DATE_TRUNC('quarter', b.sale_d), 'YYYY')||'-Q'||EXTRACT(QUARTER FROM b.sale_d)::TEXT"
        else:
            trunc_act   = "DATE_TRUNC('month', b.sale_d)::date"
            trunc_first = "DATE_TRUNC('month', MIN(b.sale_d))::date"
            period_label = "TO_CHAR(DATE_TRUNC('month', b.sale_d), 'YYYY-MM')"

        period_filter_sql, period_params = [], []
        if start_date:
            period_filter_sql.append('a.period_start >= %s::DATE'); period_params.append(start_date)
        if end_date:
            period_filter_sql.append('a.period_start <= %s::DATE'); period_params.append(end_date)
        pf = (' AND ' + ' AND '.join(period_filter_sql)) if period_filter_sql else ''

        main_sql = f"""
            WITH base AS (
                SELECT s."Customer Mobile" AS mob, s."Invoice Number" AS inv, s."Date" AS sale_d
                FROM {TABLE} s
                WHERE s."Customer Mobile" IS NOT NULL
                  AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                  AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
                  AND s."Date" IS NOT NULL {dim_sql}
            ),
            cust_first AS (SELECT b.mob, {trunc_first} AS first_bucket FROM base b GROUP BY b.mob),
            agg AS (
                SELECT {trunc_act} AS period_start, {period_label} AS period_id,
                       COUNT(DISTINCT b.mob)::bigint AS total_members,
                       COUNT(DISTINCT b.mob) FILTER (WHERE cf.first_bucket = {trunc_act})::bigint AS new_members,
                       COUNT(DISTINCT b.inv)::bigint AS total_visits
                FROM base b JOIN cust_first cf ON cf.mob = b.mob GROUP BY 1, 2
            )
            SELECT a.period_id, a.period_start, a.total_members, a.new_members, a.total_visits
            FROM agg a WHERE 1=1{pf} ORDER BY a.period_start ASC
        """
        rows_sql = _q(main_sql, list(dim_params) + list(period_params))

        db_start = 0
        if rows_sql and start_date:
            first_start = rows_sql[0][1]
            r0 = _q1(f"""
                WITH base AS (
                    SELECT s."Customer Mobile" AS mob, s."Date" AS sale_d
                    FROM {TABLE} s WHERE s."Customer Mobile" IS NOT NULL
                      AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                      AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
                      AND s."Date" IS NOT NULL {dim_sql}
                ),
                cust_first AS (SELECT b.mob, {trunc_first} AS first_bucket FROM base b GROUP BY b.mob)
                SELECT COUNT(*)::bigint FROM cust_first WHERE first_bucket < %s::DATE
            """, dim_params + [first_start])
            db_start = int(r0[0] or 0) if r0 else 0

        data, cumulative = [], db_start
        for i, row in enumerate(rows_sql):
            pid = row[0]; total_m = int(row[2] or 0); new_m = int(row[3] or 0); total_visits = int(row[4] or 0)
            repeat_m = max(0, total_m - new_m)
            mom_tm = mom_v = mom_nm = mom_rm = 0.0
            if i > 0:
                prev = data[i - 1]
                if prev['total_members']  > 0: mom_tm = (total_m - prev['total_members']) / prev['total_members'] * 100
                if prev['total_visits']   > 0: mom_v  = (total_visits - prev['total_visits']) / prev['total_visits'] * 100
                if prev['new_members']    > 0: mom_nm = (new_m - prev['new_members']) / prev['new_members'] * 100
                if prev['repeat_members'] > 0: mom_rm = (repeat_m - prev['repeat_members']) / prev['repeat_members'] * 100
            cumulative += new_m
            data.append({
                'month': pid, 'total_members': total_m, 'total_visits': total_visits,
                'new_members': new_m, 'repeat_members': repeat_m,
                'engagement_rate': round(float(total_visits / total_m if total_m else 0), 2),
                'repeat_pct': round(float(repeat_m / total_m * 100 if total_m else 0), 2),
                'mom_total_members': round(mom_tm, 2), 'mom_visits': round(mom_v, 2),
                'mom_new_members': round(mom_nm, 2), 'mom_repeat_members': round(mom_rm, 2),
                'db_size': cumulative,
            })

        cache.set(cache_key, (data, db_start), 86400)
        return data, db_start

    def get_retail_loyalty_report(self, filters):
        data, _db = self.get_retail_loyalty_matrix(filters)
        return data

    def get_retail_loyalty_advanced_report(self, filters):
        data, db_start = self.get_retail_loyalty_matrix(filters)
        return {'monthly': data, 'summary': {'db_start': db_start}}

    # ── FY Loyalty Report ────────────────────────────────────────────────────
    def get_fy_loyalty_report(self, filters):
        """
        Financial Year Loyalty Report.
        Uses pre-computed mv_fy_members / mv_fy_members_branch for instant response.
        Falls back to raw scan when MVs unavailable.
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

        # ── Fast path: pre-computed MVs ─────────────────────────────────────
        try:
            if has_branch:
                rows = _q("""
                    SELECT fy_year, total_members, new_members
                    FROM mv_fy_members_branch
                    WHERE UPPER(branch) = UPPER(%s)
                    ORDER BY fy_year ASC
                """, [branch])
            else:
                rows = _q("SELECT fy_year, total_members, new_members FROM mv_fy_members ORDER BY fy_year ASC")

            if rows:
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
                cache.set(cache_key, result, 86400)
                return result
        except Exception:
            pass  # MV not yet created — fall through to raw scan

        # ── Slow fallback: raw scan ──────────────────────────────────────────
        dim_sql_parts, dim_params = [], []
        if has_branch:
            dim_sql_parts.append('UPPER(s."Branch") = UPPER(%s)'); dim_params.append(branch)
        if filters.get('staff'):
            dim_sql_parts.append('UPPER(s."Staff") = UPPER(%s)'); dim_params.append(filters['staff'])
        if filters.get('rbm'):
            dim_sql_parts.append('UPPER(s."RBM") = UPPER(%s)'); dim_params.append(filters['rbm'])
        if filters.get('bdm'):
            dim_sql_parts.append('UPPER(s."BDM") = UPPER(%s)'); dim_params.append(filters['bdm'])
        dim_sql = (' AND ' + ' AND '.join(dim_sql_parts)) if dim_sql_parts else ''

        main_sql = f"""
            WITH base AS (
                SELECT s."Customer Mobile" AS mob, s."Date" AS sale_d
                FROM {TABLE} s
                WHERE s."Customer Mobile" IS NOT NULL
                  AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                  AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
                  AND s."Date" IS NOT NULL {dim_sql}
            ),
            cust_first AS (SELECT b.mob, MIN(b.sale_d) AS first_d FROM base b GROUP BY b.mob),
            cust_fy AS (
                SELECT b.mob,
                    CASE WHEN EXTRACT(MONTH FROM b.sale_d) >= 4 THEN EXTRACT(YEAR FROM b.sale_d) ELSE EXTRACT(YEAR FROM b.sale_d) - 1 END AS fy_year
                FROM base b GROUP BY b.mob, 2
            )
            SELECT cfy.fy_year,
                   COUNT(DISTINCT cfy.mob)::bigint AS total_members,
                   COUNT(DISTINCT cfy.mob) FILTER (
                       WHERE (CASE WHEN EXTRACT(MONTH FROM cf.first_d) >= 4 THEN EXTRACT(YEAR FROM cf.first_d) ELSE EXTRACT(YEAR FROM cf.first_d) - 1 END) = cfy.fy_year
                   )::bigint AS new_members
            FROM cust_fy cfy JOIN cust_first cf ON cf.mob = cfy.mob
            GROUP BY 1 ORDER BY 1 ASC
        """
        rows = _q(main_sql, dim_params)
        result, cumulative_db, prev_cumulative_db = [], 0, 0
        for i, row in enumerate(rows):
            fy_year = int(row[0]); total_m = int(row[1] or 0); new_m = int(row[2] or 0)
            repeat_m = max(0, total_m - new_m); cumulative_db += new_m
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
        cache.set(cache_key, result, 86400)
        return result

    # ── FY Sales Report ──────────────────────────────────────────────────────
    def get_fy_sales_report(self, filters):
        """
        Financial Year Sales Report - three-tier fast path:
          1. Django cache hit      → <1ms
          2. mv_fy_sales SELECT    → ~41ms  (global / no filter)
          3. mv_fy_sales_branch    → ~100ms (branch filter only)
          4. Raw v_sales_data scan → slow fallback for complex date-range filters
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

        # ── Fast path 1: global (no filters at all) ──────────────────────────
        if not has_date and not has_staff and not has_branch:
            try:
                rows = _q("""
                    SELECT fy_year, total_sale, total_customers, new_sale
                    FROM mv_fy_sales
                    ORDER BY fy_year ASC
                """)
                if rows:
                    result = _build_result(rows)
                    cache.set(cache_key, result, 86400)
                    return result
            except Exception:
                pass  # MV not ready, fall through

        # ── Fast path 2: branch filter only (no staff / date range) ──────────
        if has_branch and not has_date and not has_staff:
            try:
                rows = _q("""
                    SELECT fy_year, total_sale, total_customers, new_sale
                    FROM mv_fy_sales_branch
                    WHERE UPPER(branch) = UPPER(%s)
                    ORDER BY fy_year ASC
                """, [branch])
                if rows:
                    result = _build_result(rows)
                    cache.set(cache_key, result, 3600)   # 1 h (branch results)
                    return result
            except Exception:
                pass  # MV not ready, fall through

        # ── Slow path: raw v_sales_data scan (complex filter combination) ────
        dim_parts, dim_params = [], []
        if has_branch:
            dim_parts.append('UPPER(s."Branch") = UPPER(%s)'); dim_params.append(branch)
        if staff:
            dim_parts.append('UPPER(s."Staff")  = UPPER(%s)'); dim_params.append(staff)
        if rbm:
            dim_parts.append('UPPER(s."RBM")    = UPPER(%s)'); dim_params.append(rbm)
        if bdm:
            dim_parts.append('UPPER(s."BDM")    = UPPER(%s)'); dim_params.append(bdm)
        if start_date:
            dim_parts.append('s."Date" >= %s::DATE');          dim_params.append(start_date)
        if end_date:
            dim_parts.append('s."Date" <= %s::DATE');          dim_params.append(end_date)
        extra = (' AND ' + ' AND '.join(dim_parts)) if dim_parts else ''

        main_sql = f"""
            WITH base AS (
                SELECT s."Customer Mobile" AS mob,
                       s."Date"            AS sale_d,
                       s."Total Value"::FLOAT AS val
                FROM {TABLE} s
                WHERE s."Customer Mobile" IS NOT NULL
                  AND s."Customer Mobile" ~ '^[0-9]{{10}}$'
                  AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
                  AND s."Date" IS NOT NULL
                  {extra}
            ),
            cust_first AS (
                SELECT mob, MIN(sale_d) AS first_d FROM base GROUP BY mob
            ),
            agg AS (
                SELECT
                    CASE WHEN EXTRACT(MONTH FROM b.sale_d) >= 4
                         THEN EXTRACT(YEAR FROM b.sale_d)
                         ELSE EXTRACT(YEAR FROM b.sale_d) - 1
                    END AS fy_year,
                    SUM(b.val)            AS total_sale,
                    COUNT(DISTINCT b.mob) AS total_customers,
                    SUM(b.val) FILTER (
                        WHERE (CASE WHEN EXTRACT(MONTH FROM cf.first_d) >= 4
                                    THEN EXTRACT(YEAR FROM cf.first_d)
                                    ELSE EXTRACT(YEAR FROM cf.first_d) - 1
                               END)
                              = (CASE WHEN EXTRACT(MONTH FROM b.sale_d) >= 4
                                      THEN EXTRACT(YEAR FROM b.sale_d)
                                      ELSE EXTRACT(YEAR FROM b.sale_d) - 1
                                 END)
                    ) AS new_sale
                FROM base b
                JOIN cust_first cf ON cf.mob = b.mob
                GROUP BY 1
            )
            SELECT fy_year, total_sale, total_customers, new_sale
            FROM agg ORDER BY 1 ASC
        """
        try:
            rows = _q(main_sql, dim_params)
            result = _build_result(rows)
            cache.set(cache_key, result, 86400)
            return result
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f'get_fy_sales_report error: {e}')
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
