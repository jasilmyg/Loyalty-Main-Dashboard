import duckdb
import os
from django.conf import settings

# Path to the SQLite DB from previous Flask project
DB_PATH = os.path.join(settings.BASE_DIR.parent, 'project_folder', 'combined_data.db')
DUCKDB_PATH = os.path.join(settings.BASE_DIR, 'analytics.duckdb')

def CUR(n):
    try:
        return f"\u20B9{int(n):,}"
    except:
        return f"\u20B9{n}"

class AnalyticsService:
    def __init__(self):
        if os.path.exists(DUCKDB_PATH):
            self.conn = duckdb.connect(DUCKDB_PATH, read_only=True)
            self.using_native = True
        else:
            self.conn = duckdb.connect()
            sql_path = DB_PATH.replace('\\', '/')
            self.conn.execute(f"ATTACH '{sql_path}' AS sqlite_db (TYPE SQLITE);")
            self.using_native = False

    def _get_mobile_expr(self):
        return 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'
    
    def _get_date_expr(self):
        return '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME("Date", \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
    
    def _get_val_expr(self):
        return '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'

    def _build_where_clause(self, filters, prefix=""):
        conditions = []
        params = []
        # Reformat incoming dates from DD-MM-YYYY to YYYY-MM-DD if needed
        import re
        def parse_date(d_str):
            if not d_str:
                return None
            if re.match(r'^\d{2}-\d{2}-\d{4}$', d_str):
                d, m, y = d_str.split('-')
                return f"{y}-{m}-{d}"
            return d_str
            
        start_date = parse_date(filters.get('start_date'))
        end_date = parse_date(filters.get('end_date'))
        branch = filters.get('branch')
        if branch and str(branch).strip().lower() in ['all branches', 'all']:
            branch = None
            
        staff = filters.get('staff')
        rbm = filters.get('rbm')
        bdm = filters.get('bdm')
        
        # Date format in DB is either DD-MM-YYYY or a full timestamp
        date_expr = f"COALESCE(TRY_STRPTIME(CAST({prefix}\"Date\" AS VARCHAR), '%d-%m-%Y'), TRY_CAST({prefix}\"Date\" AS TIMESTAMP))"
        if start_date:
            conditions.append(f"{date_expr} >= STRPTIME(?, '%Y-%m-%d')")
            params.append(start_date)
        if end_date:
            conditions.append(f"{date_expr} <= STRPTIME(?, '%Y-%m-%d')")
            params.append(end_date)
        if branch:
            conditions.append(f"UPPER({prefix}\"Branch\") = UPPER(?)")
            params.append(branch)
        if staff:
            conditions.append(f"UPPER({prefix}\"Staff\") = UPPER(?)")
            params.append(staff)
        if rbm:
            conditions.append(f"UPPER({prefix}\"RBM\") = UPPER(?)")
            params.append(rbm)
        if bdm:
            conditions.append(f"UPPER({prefix}\"BDM\") = UPPER(?)")
            params.append(bdm)
            
        where_sql = " AND ".join(conditions) if conditions else "1=1"
        return where_sql, params

    def _get_unique_customer_count(self, where_sql, params):
        """Standardized method to count unique customers in a filtered period."""
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        query = f"""
            SELECT COUNT(DISTINCT "Customer Mobile") 
            FROM {table}
            WHERE {where_sql} AND "Customer Mobile" IS NOT NULL AND LENGTH("Customer Mobile") > 0
        """
        result = self.conn.execute(query, params).fetchone()
        return result[0] or 0

    def get_sales_overview(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME("Date", \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        query = f"""
            SELECT 
                SUM({val_expr}) as total_revenue,
                COUNT(DISTINCT "Invoice Number") as total_invoices
            FROM {table}
            WHERE {where_sql}
        """
        result = self.conn.execute(query, params).fetchone()
        if not result:
            result = (0, 0)
        tr = result[0] or 0
        ti = result[1] or 0
        atv = tr / ti if ti > 0 else 0
        
        monthly_query = f"""
            SELECT 
                STRFTIME(TRY_CAST(STRPTIME(month, '%Y-%m') AS DATE), '%b %y') as m_label,
                revenue
            FROM (
                SELECT 
                    STRFTIME({date_expr}, '%Y-%m') as month,
                    SUM({val_expr}) as revenue
                FROM {table}
                WHERE {where_sql}
                GROUP BY month
                ORDER BY month ASC
            )
        """
        monthly_data = self.conn.execute(monthly_query, params).fetchall()
        
        return {
            "total_revenue": tr,
            "total_invoices": ti,
            "atv": atv,
            "monthly_trend": [{"month": m[0], "revenue": m[1]} for m in monthly_data]
        }

    def get_customer_analytics(self, filters):
        where_sql, params = self._build_where_clause(filters)
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        
        # Consistent mobile logic: same as frequency distribution table
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'
        
        # Get unique customer count using the numeric logic
        query_total = f"""
            SELECT COUNT(DISTINCT {mobile_expr})
            FROM {table}
            WHERE {where_sql} AND {mobile_expr} IS NOT NULL
        """
        total_customers = self.conn.execute(query_total, params).fetchone()[0] or 0
        
        # We need historical all-time visits for the "Repeat Customers" calculation,
        # but only for the customers who have transactions within the filtered period.
        query = f"""
            WITH filtered_customers AS (
                SELECT 
                    {mobile_expr} as mobile_num,
                    SUM({val_expr}) as period_ltv
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            ),
            all_time_visits AS (
                SELECT 
                    {mobile_expr} as mobile_num,
                    COUNT(DISTINCT CAST({date_expr} AS DATE)) as total_historical_visits
                FROM {table}
                WHERE {mobile_expr} IN (SELECT mobile_num FROM filtered_customers)
                GROUP BY {mobile_expr}
            )
            SELECT 
                f.mobile_num,
                f.period_ltv,
                a.total_historical_visits
            FROM filtered_customers f
            JOIN all_time_visits a ON f.mobile_num = a.mobile_num
        """
        rows = self.conn.execute(query, params).fetchall()
        
        total_ltv = sum((r[1] or 0) for r in rows)
        # Repeat customer: if their all-time visits > 1
        repeat_customers = sum(1 for r in rows if r[2] and r[2] > 1)
        repeat_rate = (repeat_customers / total_customers * 100) if total_customers > 0 else 0
        
        return {
            "total_ltv": total_ltv,
            "total_customers": total_customers,
            "repeat_customers": repeat_customers,
            "repeat_purchase_rate": repeat_rate
        }

    def get_frequency_distribution(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME("Date", \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'
        
        query = f"""
            WITH customer_stats AS (
                SELECT
                    CAST({mobile_expr} AS VARCHAR) AS "Customer Mobile",
                    COUNT(DISTINCT CAST({date_expr} AS DATE)) AS visits,
                    SUM({val_expr}) AS revenue
                FROM {table}
                WHERE {where_sql}
                  AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            ),
            bucketed AS (
                SELECT
                    CASE
                        WHEN visits = 1   THEN '1 Visit'
                        WHEN visits = 2   THEN '2 Visits'
                        WHEN visits = 3   THEN '3 Visits'
                        WHEN visits = 4   THEN '4 Visits'
                        WHEN visits BETWEEN 5  AND 9   THEN '5-9 Visits'
                        WHEN visits BETWEEN 10 AND 20  THEN '10-20 Visits'
                        WHEN visits BETWEEN 21 AND 50  THEN '21-50 Visits'
                        WHEN visits BETWEEN 51 AND 100 THEN '51-100 Visits'
                        ELSE 'Above 100 Visits'
                    END AS segment,
                    visits,
                    revenue
                FROM customer_stats
            )
            SELECT
                segment,
                COUNT(*)                                              AS customers,
                COALESCE(SUM(revenue), 0)                             AS net_revenue,
                COUNT(*) * 100.0 / SUM(COUNT(*)) OVER ()             AS cust_pct,
                COALESCE(SUM(revenue), 0) * 100.0 /
                    NULLIF(SUM(SUM(revenue)) OVER (), 0)              AS rev_pct,
                COALESCE(SUM(revenue), 0) / NULLIF(COUNT(*), 0)      AS asp
            FROM bucketed
            GROUP BY segment
            ORDER BY
                CASE segment
                    WHEN '1 Visit'          THEN 1
                    WHEN '2 Visits'         THEN 2
                    WHEN '3 Visits'         THEN 3
                    WHEN '4 Visits'         THEN 4
                    WHEN '5-9 Visits'       THEN 5
                    WHEN '10-20 Visits'     THEN 6
                    WHEN '21-50 Visits'     THEN 7
                    WHEN '51-100 Visits'    THEN 8
                    ELSE 9
                END
        """
        rows = self.conn.execute(query, params).fetchall()
        return [
            {
                "segment": r[0],
                "customers": r[1],
                "net_revenue": round(r[2] or 0, 2),
                "cust_pct": round(r[3] or 0, 2),
                "rev_pct": round(r[4] or 0, 2),
                "asp": round(r[5] or 0, 2),
            }
            for r in rows
        ]


    SEGMENT_CHUNK_SIZE = 1_000_000   # rows per Excel part

    # Segment label → visit-count predicate
    _SEGMENT_FILTER = {
        '1 Visit':          'visits = 1',
        '2 Visits':         'visits = 2',
        '3 Visits':         'visits = 3',
        '4 Visits':         'visits = 4',
        '5-9 Visits':       'visits BETWEEN 5 AND 9',
        '10-20 Visits':     'visits BETWEEN 10 AND 20',
        '21-50 Visits':     'visits BETWEEN 21 AND 50',
        '51-100 Visits':    'visits BETWEEN 51 AND 100',
        'Above 100 Visits': 'visits > 100',
    }

    def get_customers_for_segment(self, filters, segment, offset=0):
        """Return up to SEGMENT_CHUNK_SIZE customer rows for one visit segment."""
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        val_expr = ('"Total Value"' if self.using_native
                    else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)')
        date_expr = ('"Date"' if self.using_native
                     else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))')

        seg_predicate = self._SEGMENT_FILTER.get(segment, '1=0')

        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH customer_stats AS (
                SELECT
                    CAST({mobile_expr} AS VARCHAR) AS "Customer Mobile",
                    MAX("Customer Name")          AS customer_name,
                    COUNT(DISTINCT CAST({date_expr} AS DATE))   AS visits,
                    SUM({val_expr})               AS net_revenue,
                    MAX({date_expr})              AS last_visit
                FROM {table}
                WHERE {where_sql}
                  AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            )
            SELECT
                "Customer Mobile",
                customer_name,
                visits,
                net_revenue,
                CAST(last_visit AS DATE) AS last_visit_date
            FROM customer_stats
            WHERE {seg_predicate}
            ORDER BY net_revenue DESC NULLS LAST
            LIMIT {self.SEGMENT_CHUNK_SIZE} OFFSET {offset}
        """
        result = self.conn.execute(query, params)
        headers = [d[0] for d in result.description]
        rows    = result.fetchall()
        return headers, rows

    def count_customers_for_segment(self, filters, segment):
        """Return total customer count for one visit segment (for part calculation)."""
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = ('"Date"' if self.using_native
                     else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))')

        seg_predicate = self._SEGMENT_FILTER.get(segment, '1=0')

        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH customer_stats AS (
                SELECT
                    {mobile_expr} AS mobile,
                    COUNT(DISTINCT CAST({date_expr} AS DATE)) AS visits
                FROM {table}
                WHERE {where_sql}
                  AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            )
            SELECT COUNT(*) FROM customer_stats WHERE {seg_predicate}
        """
        result = self.conn.execute(query, params).fetchone()
        return result[0] if result else 0

    def _get_rfm_base_cte(self, where_sql):
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'
        
        return f"""
            WITH rfm_base AS (
                SELECT 
                    {mobile_expr} as mobile,
                    MAX("Customer Name") as customer_name,
                    DATE_DIFF('day', MAX(CAST({date_expr} AS DATE)), CURRENT_DATE) as recency,
                    COUNT(DISTINCT CAST({date_expr} AS DATE)) as frequency,
                    SUM({val_expr}) as monetary,
                    MAX(CAST({date_expr} AS DATE)) as last_visit
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            ),
            scored AS (
                SELECT 
                    mobile,
                    customer_name,
                    recency,
                    frequency,
                    monetary,
                    last_visit,
                    CASE 
                        WHEN recency <= 90 THEN 5
                        WHEN recency <= 180 THEN 4
                        WHEN recency <= 365 THEN 3
                        WHEN recency <= 730 THEN 2
                        ELSE 1
                    END as r_score,
                    CASE 
                        WHEN frequency >= 5 THEN 5
                        WHEN frequency = 4 THEN 4
                        WHEN frequency = 3 THEN 3
                        WHEN frequency = 2 THEN 2
                        ELSE 1
                    END as f_score,
                    -- NTILE(5) assigns 1 to bottom 20% and 5 to top 20% (ascending order)
                    NTILE(5) OVER (ORDER BY monetary ASC) as m_score
                FROM rfm_base
            ),
            segmented AS (
                SELECT
                    *,
                    CAST(r_score AS VARCHAR) || CAST(f_score AS VARCHAR) || CAST(m_score AS VARCHAR) as rfm_code,
                    CASE
                        -- 🏆 Champions (Best customers)
                        WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
                        
                        -- 💙 Loyal (Strong repeat buyers)
                        WHEN r_score >= 3 AND f_score >= 3 AND m_score >= 3 THEN 'Loyal'
                        
                        -- 🆕 New (Recent but low frequency)
                        WHEN r_score >= 4 AND f_score <= 2 THEN 'New'
                        
                        -- ⚠️ At Risk (Visited between 1 and 2 year, high frequency/spend)
                        WHEN r_score = 2 AND f_score >= 3 AND m_score >= 3 THEN 'At Risk'
                        
                        -- ❌ Lost (Not visited 2+ years)
                        WHEN r_score = 1 THEN 'Lost'
                        
                        -- 🟡 Others (Potential, Average, Need Attention) - Catch-all for mid-engagement
                        ELSE 'Others'
                    END as segment
                FROM scored
            )
        """

    def get_rfm_segments(self, filters):
        where_sql, params = self._build_where_clause(filters)
        cte = self._get_rfm_base_cte(where_sql)
        
        query = f"""
            {cte}
            SELECT 
                segment, 
                COUNT(mobile) as count,
                SUM(monetary) as total_revenue,
                AVG(monetary) as avg_revenue
            FROM segmented
            GROUP BY segment
            ORDER BY count DESC
        """
        data = self.conn.execute(query, params).fetchall()
        return [{
            "segment": r[0], 
            "count": r[1],
            "total_revenue": r[2] or 0,
            "avg_revenue": r[3] or 0
        } for r in data]

    def get_monetary_quintiles(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH customer_stats AS (
                SELECT 
                    {mobile_expr} as mobile,
                    SUM({val_expr}) as total_spend
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            ),
            scored AS (
                SELECT 
                    total_spend,
                    NTILE(5) OVER (ORDER BY total_spend DESC) as quintile
                FROM customer_stats
            )
            SELECT 
                quintile,
                AVG(total_spend) as avg_spend,
                COUNT(*) as customer_count
            FROM scored
            GROUP BY quintile
            ORDER BY quintile ASC
        """
        data = self.conn.execute(query, params).fetchall()
        
        labels = {
            1: "Top 20%",
            2: "Next 20%",
            3: "Middle 20%",
            4: "Next 20%",
            5: "Bottom 20%"
        }
        
        return [{
            "label": labels.get(r[0], f"Group {r[0]}"),
            "avg_spend": r[1] or 0,
            "count": r[2]
        } for r in data]

    def get_rfm_details_query(self, filters, segment=None):
        where_sql, params = self._build_where_clause(filters)
        cte = self._get_rfm_base_cte(where_sql)
        
        where_seg = ""
        if segment:
            where_seg = "WHERE segment = ?"
            params.append(segment)
            
        query = f"""
            {cte}
            SELECT 
                customer_name as "Customer Name", 
                mobile as "Customer Mobile", 
                recency as "Recency (Days)", 
                frequency as "Frequency (Visits)", 
                monetary as "Monetary Value",
                r_score as "R Score",
                f_score as "F Score",
                m_score as "M Score",
                rfm_code as "RFM Code",
                segment as "RFM Segment",
                last_visit as "Last Visit Date"
            FROM segmented
            {where_seg}
            ORDER BY monetary DESC NULLS LAST
        """
        return query, params

    def perform_rfm_analysis(self, filters):
        """Alias kept for backward-compatibility with views.py."""
        return self.get_rfm_segments(filters)

    def get_cohort_retention(self):
        # We generally do cohort retention across the whole dataset without strict date/branch filters
        # because cohort sizes need the full history of the user to identify their "first month".
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME("Date", \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        query = f"""
            WITH cohort_items AS (
                SELECT "Customer Mobile",
                       MIN(STRFTIME({date_expr}, '%Y-%m')) as cohort_month
                FROM {table}
                WHERE "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
                GROUP BY "Customer Mobile"
            ),
            user_activities AS (
                SELECT a."Customer Mobile",
                       DATE_DIFF('month', 
                           STRPTIME(c.cohort_month || '-01', '%Y-%m-%d'),
                           STRPTIME(STRFTIME({date_expr}, '%Y-%m') || '-01', '%Y-%m-%d')
                       ) as month_number,
                       c.cohort_month
                FROM {table} a
                JOIN cohort_items c ON a."Customer Mobile" = c."Customer Mobile"
                WHERE a."Customer Mobile" IS NOT NULL AND a."Customer Mobile" != ''
            )
            SELECT cohort_month, month_number, COUNT(DISTINCT a."Customer Mobile") as num_users
            FROM user_activities a
            GROUP BY cohort_month, month_number
            ORDER BY cohort_month, month_number
        """
        data = self.conn.execute(query).fetchall()
        
        cohorts = {}
        for row in data:
            c_month, m_num, count = row
            if c_month not in cohorts:
                cohorts[c_month] = {}
            cohorts[c_month][m_num] = count
            
        return {"cohorts": cohorts}

    def get_yearly_cohort_analysis(self):
        """Perform yearly cohort analysis returning retention, revenue, LTV, and RFM health."""
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME("Date", \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH customer_first_visit AS (
                SELECT {mobile_expr} as mobile, MIN(CAST({date_expr} AS DATE)) as first_date
                FROM {table}
                WHERE {mobile_expr} IS NOT NULL
                GROUP BY mobile
            ),
            customer_activities AS (
                SELECT 
                    {mobile_expr} as mobile,
                    CAST({date_expr} AS DATE) as activity_date,
                    {val_expr} as revenue,
                    f.first_date,
                    STRFTIME(f.first_date, '%Y') as cohort_year,
                    DATE_DIFF('year', 
                        STRPTIME(STRFTIME(f.first_date, '%Y') || '-01-01', '%Y-%m-%d'),
                        STRPTIME(STRFTIME(CAST({date_expr} AS DATE), '%Y') || '-01-01', '%Y-%m-%d')
                    ) as year_index
                FROM {table} s
                JOIN customer_first_visit f ON {mobile_expr} = f.mobile
            ),
            cohort_yearly_stats AS (
                SELECT 
                    cohort_year,
                    year_index,
                    COUNT(DISTINCT mobile) as active_customers,
                    SUM(revenue) as year_revenue
                FROM customer_activities
                GROUP BY cohort_year, year_index
            ),
            cohort_base_size AS (
                SELECT cohort_year, active_customers as initial_size
                FROM cohort_yearly_stats
                WHERE year_index = 0
            ),
            -- Add RFM snapshots per cohort (Current RFM state)
            cohort_rfm AS (
                SELECT 
                    STRFTIME(f.first_date, '%Y') as c_year,
                    CASE
                        -- Reuse simplified RFM logic for speed
                        WHEN DATE_DIFF('day', MAX(CAST({date_expr} AS DATE)), CURRENT_DATE) <= 90 AND COUNT(DISTINCT CAST({date_expr} AS DATE)) >= 3 THEN 'Champions'
                        WHEN DATE_DIFF('day', MAX(CAST({date_expr} AS DATE)), CURRENT_DATE) <= 180 AND COUNT(DISTINCT CAST({date_expr} AS DATE)) >= 2 THEN 'Loyal'
                        WHEN DATE_DIFF('day', MAX(CAST({date_expr} AS DATE)), CURRENT_DATE) > 365 THEN 'Lost'
                        ELSE 'Average'
                    END as segment,
                    COUNT(DISTINCT {mobile_expr}) as cust_count
                FROM {table} s
                JOIN customer_first_visit f ON {mobile_expr} = f.mobile
                GROUP BY c_year, segment
            )
            SELECT 
                s.cohort_year,
                s.year_index,
                s.active_customers,
                s.year_revenue,
                b.initial_size,
                (s.active_customers * 100.0 / b.initial_size) as retention_rate
            FROM cohort_yearly_stats s
            JOIN cohort_base_size b ON s.cohort_year = b.cohort_year
            ORDER BY s.cohort_year DESC, s.year_index ASC
        """
        
        # Execute main stats
        rows = self.conn.execute(query).fetchall()
        
        # Structure the results
        cohort_data = {}
        for r in rows:
            cy, yi, active, rev, size, rate = r
            if cy not in cohort_data:
                cohort_data[cy] = {"size": size, "years": {}}
            cohort_data[cy]["years"][yi] = {
                "active": active,
                "revenue": round(rev or 0, 2),
                "retention": round(rate, 2),
                "ltv": round(rev / size, 2) if size > 0 else 0
            }

        # Fetch RFM distribution
        rfm_query = """
            WITH customer_first_visit AS (
                SELECT CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT) as mobile, MIN(CAST("Date" AS DATE)) as first_date
                FROM sales_data
                GROUP BY mobile
            ),
            rfm_stats AS (
                SELECT 
                    STRFTIME(f.first_date, '%Y') as c_year,
                    CASE
                        WHEN DATE_DIFF('day', MAX(CAST(s."Date" AS DATE)), CURRENT_DATE) <= 90 AND COUNT(DISTINCT CAST(s."Date" AS DATE)) >= 3 THEN 'Champions'
                        WHEN DATE_DIFF('day', MAX(CAST(s."Date" AS DATE)), CURRENT_DATE) <= 180 AND COUNT(DISTINCT CAST(s."Date" AS DATE)) >= 2 THEN 'Loyal'
                        WHEN DATE_DIFF('day', MAX(CAST(s."Date" AS DATE)), CURRENT_DATE) > 365 THEN 'Lost'
                        ELSE 'Average'
                    END as segment
                FROM sales_data s
                JOIN customer_first_visit f ON CAST(TRY_CAST(s."Customer Mobile" AS DOUBLE) AS BIGINT) = f.mobile
                GROUP BY f.mobile, c_year
            )
            SELECT c_year, segment, COUNT(*) 
            FROM rfm_stats 
            GROUP BY c_year, segment
        """
        rfm_rows = self.conn.execute(rfm_query).fetchall()
        for rr in rfm_rows:
            cy, seg, count = rr
            if cy in cohort_data:
                if "rfm" not in cohort_data[cy]:
                    cohort_data[cy]["rfm"] = {}
                cohort_data[cy]["rfm"][seg] = count

        return cohort_data
        
    def get_payment_analytics(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        
        payment_cols = [
            'Cash', 'Debit Card', 'Credit Card', 'Benow', 'Advance Receipt', 
            'Bharath QR', 'Paytm QR', 'Pine Labs QR', 'UPI Cashback', 
            'Gift Voucher', 'Approved Credit', 'EMI'
        ]
        
        selects = []
        for col in payment_cols:
            col_expr = f'TRY_CAST(REPLACE(REPLACE("{col}", \',\', \'\'), \' \', \'\') AS DOUBLE)'
            selects.append(f'SUM({col_expr}) as sum_{col.replace(" ", "_").replace("(", "").replace(")", "")}')
            
        query = f"""
            SELECT 
                {", ".join(selects)}
            FROM {table}
            WHERE {where_sql}
        """
        result = self.conn.execute(query, params).fetchone()
        
        distribution = {}
        for i, col in enumerate(payment_cols):
            val = result[i] if result and result[i] else 0
            if val > 0:
                distribution[col] = val
                
        return {"distribution": distribution}

    def get_discount_analysis(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        
        discount_cols = [
            'Discount', 'Indirect Discount', 'Exchange', 'Buyback', 
            'Addition', 'Deduction', 'POINT REDUMPTION (DEDUCTION)', 'RISK POOL (DEDUCTION)'
        ]
        
        selects = [f'SUM(TRY_CAST(REPLACE(REPLACE("{col}", \',\', \'\'), \' \', \'\') AS DOUBLE)) as sum_{i}' for i, col in enumerate(discount_cols)]
        
        query = f"""
            SELECT 
                {", ".join(selects)}
            FROM {table}
            WHERE {where_sql}
        """
        result = self.conn.execute(query, params).fetchone()
        
        distribution = {}
        for i, col in enumerate(discount_cols):
            val = result[i] if result and result[i] else 0
            if val != 0:
                distribution[col] = val
                
        return {"distribution": distribution}

    def get_staff_performance(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        query = f"""
            SELECT 
                "Staff", "Staff Code",
                SUM({val_expr}) as sales_value,
                COUNT(DISTINCT CAST("Date" AS DATE)) as invoice_count,
                SUM({val_expr}) / NULLIF(COUNT(DISTINCT CAST("Date" AS DATE)), 0) as atv
            FROM {table}
            WHERE {where_sql} AND "Staff" IS NOT NULL AND "Staff" != ''
            GROUP BY "Staff", "Staff Code"
            ORDER BY sales_value DESC NULLS LAST
            LIMIT 50
        """
        data = self.conn.execute(query, params).fetchall()
        return [{"staff": r[0], "code": r[1], "sales": r[2], "invoices": r[3], "atv": r[4]} for r in data]

    def get_branch_performance(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        query = f"""
            SELECT 
                "Branch",
                SUM({val_expr}) as revenue,
                COUNT(DISTINCT CAST("Date" AS DATE)) as transactions,
                COUNT(DISTINCT "Customer Mobile") as customer_count,
                SUM({val_expr}) / NULLIF(COUNT(DISTINCT CAST("Date" AS DATE)), 0) as atv
            FROM {table}
            WHERE {where_sql} AND "Branch" IS NOT NULL AND "Branch" != ''
            GROUP BY "Branch"
            ORDER BY revenue DESC NULLS LAST
        """
        data = self.conn.execute(query, params).fetchall()
        return [{"branch": r[0], "revenue": r[1], "transactions": r[2], "customers": r[3], "atv": r[4]} for r in data]

    def _get_gap_analysis_base_cte(self, where_sql):
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'
        
        return f"""
            WITH raw_purchases AS (
                SELECT 
                    {mobile_expr} as "Customer Mobile",
                    "Invoice Number",
                    "Branch",
                    "Staff",
                    CAST({date_expr} AS DATE) as purchase_date,
                    {val_expr} as sales_value
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
            ),
            customer_purchases AS (
                SELECT 
                    "Customer Mobile",
                    MAX("Branch") as "Branch",
                    MAX("Staff") as "Staff",
                    purchase_date,
                    SUM(sales_value) as daily_sales
                FROM raw_purchases
                GROUP BY "Customer Mobile", purchase_date
            ),
            ranked_purchases AS (
                SELECT 
                    *,
                    LAG(purchase_date) OVER(PARTITION BY "Customer Mobile" ORDER BY purchase_date) as prev_purchase_date
                FROM customer_purchases
            ),
            gap_data AS (
                SELECT 
                    *,
                    DATE_DIFF('day', prev_purchase_date, purchase_date) as gap_days
                FROM ranked_purchases
                WHERE prev_purchase_date IS NOT NULL
            )
        """
        
    def get_gap_segmentation(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH raw_purchases AS (
                SELECT 
                    {mobile_expr} as mobile,
                    CAST({date_expr} AS DATE) as purchase_date,
                    {val_expr} as sales_value
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
            ),
            daily_visits AS (
                SELECT mobile, purchase_date
                FROM raw_purchases
                GROUP BY mobile, purchase_date
            ),
            ranked AS (
                SELECT 
                    mobile,
                    purchase_date,
                    LAG(purchase_date) OVER(PARTITION BY mobile ORDER BY purchase_date) as prev_date
                FROM daily_visits
            ),
            gaps AS (
                SELECT 
                    mobile,
                    DATE_DIFF('day', prev_date, purchase_date) as gap_days
                FROM ranked
                WHERE prev_date IS NOT NULL
            ),
            customer_avg_gaps AS (
                SELECT 
                    mobile,
                    AVG(gap_days) as gap_days
                FROM gaps
                GROUP BY mobile
            ),
            bucketed AS (
                SELECT 
                    mobile,
                    gap_days,
                    CASE
                        WHEN gap_days <= 7    THEN '1-7 Days'
                        WHEN gap_days <= 30   THEN '8-30 Days'
                        WHEN gap_days <= 60   THEN '31-60 Days'
                        WHEN gap_days <= 90   THEN '61-90 Days'
                        WHEN gap_days <= 180  THEN '91-180 Days'
                        WHEN gap_days <= 365  THEN '180-365 Days'
                        WHEN gap_days <= 730  THEN '1-2 Years'
                        WHEN gap_days <= 1095 THEN '2-3 Years'
                        WHEN gap_days <= 1460 THEN '3-4 Years'
                        ELSE '4+ Years'
                    END as gap_range,
                    CASE
                        WHEN gap_days <= 7    THEN 'Very High'
                        WHEN gap_days <= 30   THEN 'High'
                        WHEN gap_days <= 60   THEN 'High'
                        WHEN gap_days <= 90   THEN 'Medium'
                        WHEN gap_days <= 180  THEN 'Medium'
                        WHEN gap_days <= 365  THEN 'Low'
                        WHEN gap_days <= 730  THEN 'Very Low'
                        WHEN gap_days <= 1095 THEN 'Very Low'
                        WHEN gap_days <= 1460 THEN 'Very Low'
                        ELSE 'Very Low'
                    END as signal,
                    CASE
                        WHEN gap_days <= 7    THEN 1
                        WHEN gap_days <= 30   THEN 2
                        WHEN gap_days <= 60   THEN 3
                        WHEN gap_days <= 90   THEN 4
                        WHEN gap_days <= 180  THEN 5
                        WHEN gap_days <= 365  THEN 6
                        WHEN gap_days <= 730  THEN 7
                        WHEN gap_days <= 1095 THEN 8
                        WHEN gap_days <= 1460 THEN 9
                        ELSE 10
                    END as sort_order
                FROM gaps
            )
            SELECT
                gap_range,
                COUNT(DISTINCT mobile) as customers,
                COUNT(DISTINCT mobile) * 100.0 / SUM(COUNT(DISTINCT mobile)) OVER() as percentage,
                AVG(gap_days) as avg_gap,
                signal,
                sort_order,
                CASE
                    WHEN sort_order = 1  THEN 'Immediate'
                    WHEN sort_order = 2  THEN 'High'
                    WHEN sort_order = 3  THEN 'Medium'
                    WHEN sort_order = 4  THEN 'Medium'
                    WHEN sort_order = 5  THEN 'Critical'
                    WHEN sort_order = 6  THEN 'Reactivate'
                    WHEN sort_order = 7  THEN 'Reactivate'
                    WHEN sort_order >= 8 THEN 'Ignore'
                END as priority,
                CASE
                    WHEN sort_order = 1  THEN 'Upsell immediately. Propose complementary products.'
                    WHEN sort_order = 2  THEN 'Send gentle reminder (WhatsApp/SMS) about new stock.'
                    WHEN sort_order = 3  THEN 'Campaign push. Feature limited-time collection.'
                    WHEN sort_order = 4  THEN 'Offer / Bundle deal to stimulate visit.'
                    WHEN sort_order = 5  THEN 'Win-back campaign. Personalized "We miss you" message.'
                    WHEN sort_order = 6  THEN 'Strong discount + emotional message tailored to past purchases.'
                    WHEN sort_order = 7  THEN 'Reactivation campaign. Ask for feedback or survey.'
                    WHEN sort_order = 8  THEN 'Selective targeting only for high-value past spenders.'
                    WHEN sort_order = 9  THEN 'Selective targeting. Low ROI probability.'
                    ELSE 'Ignore / Archive. Customer likely churned.'
                END as action_strategy
            FROM bucketed
            GROUP BY gap_range, signal, sort_order
            ORDER BY sort_order ASC
        """
        rows = self.conn.execute(query, params).fetchall()
        return [{
            "segment": r[0],
            "count": r[1],
            "percentage": round(r[2] or 0, 2),
            "avg_gap": round(r[3] or 0, 1),
            "signal": r[4],
            "priority": r[6],
            "action": r[7],
        } for r in rows]

    def get_customer_segmentation_matrix(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH customer_stats AS (
                SELECT
                    {mobile_expr} as mobile,
                    COUNT(DISTINCT CAST({date_expr} AS DATE)) as visits,
                    SUM({val_expr}) as total_spend,
                    DATE_DIFF('day', MAX(CAST({date_expr} AS DATE)), CURRENT_DATE) as recency_days
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            )
            SELECT
                CASE WHEN visits = 1 THEN 'One-Time' WHEN visits <= 3 THEN 'Occasional' ELSE 'Frequent' END as freq_seg,
                CASE WHEN recency_days <= 90 THEN 'Active' WHEN recency_days <= 365 THEN 'Lapsing' ELSE 'Inactive' END as rec_seg,
                COUNT(*) as customers,
                AVG(total_spend) as avg_spend
            FROM customer_stats
            GROUP BY freq_seg, rec_seg
            ORDER BY freq_seg, rec_seg
        """
        rows = self.conn.execute(query, params).fetchall()
        return [{"freq": r[0], "recency": r[1], "customers": r[2], "avg_spend": round(r[3] or 0, 2)} for r in rows]

    def get_action_engine_data(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH customer_stats AS (
                SELECT
                    {mobile_expr} as mobile,
                    COUNT(DISTINCT CAST({date_expr} AS DATE)) as visits,
                    SUM({val_expr}) as total_spend,
                    DATE_DIFF('day', MAX(CAST({date_expr} AS DATE)), CURRENT_DATE) as recency_days
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}
            )
            SELECT
                'Lapsing High Value' as segment,
                COUNT(*) as customers,
                SUM(total_spend) as revenue_at_risk,
                'Send Win-Back SMS with custom discount' as action
            FROM customer_stats
            WHERE recency_days BETWEEN 90 AND 180 AND total_spend >= 10000
            
            UNION ALL
            
            SELECT
                'Recently Active' as segment,
                COUNT(*) as customers,
                SUM(total_spend) as revenue_at_risk,
                'Nurture with product feedback loop' as action
            FROM customer_stats
            WHERE recency_days <= 30 AND visits = 1
            
            UNION ALL
            
            SELECT
                'Frequent Shoppers at Risk' as segment,
                COUNT(*) as customers,
                SUM(total_spend) as revenue_at_risk,
                'Trigger premium loyalty offer' as action
            FROM customer_stats
            WHERE recency_days BETWEEN 45 AND 90 AND visits >= 3
        """
        rows = self.conn.execute(query, params).fetchall()
        return [{
            "segment": r[0],
            "customers": r[1],
            "revenue_at_risk": round(r[2] or 0, 2),
            "action": r[3]
        } for r in rows if r[1] > 0]

    def get_business_insights(self, filters):
        return []

    def get_cohort_business_insights(self):
        return []

    def get_loyalty_overview_kpis(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = '"Date"' if self.using_native else 'COALESCE(TRY_STRPTIME(CAST("Date" AS VARCHAR), \'%d-%m-%Y\'), TRY_CAST("Date" AS TIMESTAMP))'
        val_expr = '"Total Value"' if self.using_native else 'TRY_CAST(REPLACE(REPLACE("Total Value", \',\', \'\'), \' \', \'\') AS DOUBLE)'
        mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

        query = f"""
            WITH daily_visits AS (
                SELECT
                    {mobile_expr} as mobile,
                    CAST({date_expr} AS DATE) as visit_date,
                    {val_expr} as sales_value
                FROM {table}
                WHERE {where_sql} AND {mobile_expr} IS NOT NULL
                GROUP BY {mobile_expr}, CAST({date_expr} AS DATE), {val_expr}
            ),
            customer_stats AS (
                SELECT
                    mobile,
                    COUNT(DISTINCT visit_date) as visits,
                    SUM(sales_value) as total_spend,
                    MAX(visit_date) as last_visit
                FROM daily_visits
                GROUP BY mobile
            ),
            gap_data AS (
                SELECT
                    mobile,
                    visit_date,
                    LAG(visit_date) OVER(PARTITION BY mobile ORDER BY visit_date) as prev_date
                FROM daily_visits
            ),
            customer_avg_gaps AS (
                SELECT 
                    mobile,
                    AVG(DATE_DIFF('day', prev_date, visit_date)) as avg_gap_days
                FROM gap_data
                WHERE prev_date IS NOT NULL
                GROUP BY mobile
            )
            SELECT
                COUNT(DISTINCT c.mobile) as total_customers,
                COUNT(DISTINCT CASE WHEN c.visits > 1 THEN c.mobile END) as repeat_customers,
                AVG(g.avg_gap_days) as avg_gap
            FROM customer_stats c
            LEFT JOIN customer_avg_gaps g ON c.mobile = g.mobile
        """
        row = self.conn.execute(query, params).fetchone()
        if not row:
            return {"total_customers": 0, "repeat_customers": 0, "repeat_rate": 0, "avg_gap": 0}
        total = row[0] or 0
        repeat = row[1] or 0
        return {
            "total_customers": total,
            "repeat_customers": repeat,
            "repeat_rate": round(repeat / total * 100, 1) if total > 0 else 0,
            "avg_gap": round(row[2] or 0, 1),
        }

    def get_unique_branches(self):
        rows = self.conn.execute('SELECT DISTINCT "Branch" FROM sales_data WHERE "Branch" IS NOT NULL AND "Branch" != \'\' ORDER BY "Branch"').fetchall()
        return [r[0] for r in rows]


        
    def get_retail_loyalty_report(self, filters):
        where_sql, params = self._build_where_clause(filters)
        table = "sales_data" if self.using_native else "sqlite_db.sales_data"
        date_expr = self._get_date_expr()
        mobile_expr = self._get_mobile_expr()
        
        query = f"""
            WITH customer_first_visit AS (
                SELECT 
                    CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT) as mobile,
                    DATE_TRUNC('month', MIN(CAST({date_expr} AS DATE))) as first_month
                FROM {table}
                WHERE CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT) IS NOT NULL
                GROUP BY CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)
            ),
            filtered_sales AS (
                SELECT 
                    CAST(TRY_CAST(s."Customer Mobile" AS DOUBLE) AS BIGINT) as mobile,
                    CAST(s.{date_expr} AS DATE) as visit_date,
                    DATE_TRUNC('month', CAST(s.{date_expr} AS DATE)) as visit_month
                FROM {table} s
                WHERE {where_sql} AND CAST(TRY_CAST(s."Customer Mobile" AS DOUBLE) AS BIGINT) IS NOT NULL
            ),
            monthly_activity AS (
                SELECT 
                    STRFTIME(s.visit_month, '%Y-%m') as month_id,
                    COUNT(DISTINCT s.mobile) as total_members,
                    COUNT(DISTINCT s.visit_date) as total_visits,
                    COUNT(DISTINCT CASE WHEN s.visit_month = f.first_month THEN s.mobile END) as new_members,
                    COUNT(DISTINCT CASE WHEN s.visit_month > f.first_month THEN s.mobile END) as repeat_members
                FROM filtered_sales s
                JOIN customer_first_visit f ON s.mobile = f.mobile
                GROUP BY s.visit_month
                ORDER BY s.visit_month ASC
            )
            SELECT 
                month_id,
                total_members,
                total_visits,
                new_members,
                repeat_members,
                CAST(total_visits AS DOUBLE) / NULLIF(total_members, 0) as engagement_rate,
                CAST(repeat_members AS DOUBLE) / NULLIF(total_members, 0) * 100 as repeat_pct
            FROM monthly_activity
        """
        rows = self.conn.execute(query, params).fetchall()
        
        data = []
        
        # Get starting DB size before the filtered period
        start_date = filters.get('start_date')
        initial_db_size = 0
        if start_date:
            import re
            parsed_start = start_date
            if re.match(r'^\d{2}-\d{2}-\d{4}$', start_date):
                d, m, y = start_date.split('-')
                parsed_start = f"{y}-{m}-{d}"
            
            db_size_query = f"""
                SELECT COUNT(DISTINCT {mobile_expr})
                FROM {table}
                WHERE {date_expr} < STRPTIME(?, '%Y-%m-%d') AND {mobile_expr} IS NOT NULL
            """
            branch_cond = ""
            branch_params = []
            if filters.get('branch'):
                branch_cond = f" AND UPPER(\"Branch\") = UPPER(?)"
                branch_params = [filters.get('branch')]
            
            initial_db_size = self.conn.execute(db_size_query + branch_cond, [parsed_start] + branch_params).fetchone()[0] or 0

        cumulative_new_members = initial_db_size
        
        for i, r in enumerate(rows):
            month_id, total_m, total_v, new_m, repeat_m, eng_rate, rep_pct = r
            
            mom_total_m = 0
            mom_visits = 0
            mom_new_m = 0
            mom_repeat_m = 0
            
            if i > 0:
                prev = data[i-1]
                if prev['total_members'] > 0:
                    mom_total_m = (total_m - prev['total_members']) / prev['total_members'] * 100
                if prev['total_visits'] > 0:
                    mom_visits = (total_v - prev['total_visits']) / prev['total_visits'] * 100
                if prev['new_members'] > 0:
                    mom_new_m = (new_m - prev['new_members']) / prev['new_members'] * 100
                if prev['repeat_members'] > 0:
                    mom_repeat_m = (repeat_m - prev['repeat_members']) / prev['repeat_members'] * 100
            
            cumulative_new_members += new_m
            
            data.append({
                "month": month_id,
                "total_members": total_m,
                "total_visits": total_v,
                "new_members": new_m,
                "repeat_members": repeat_m,
                "engagement_rate": round(eng_rate or 0, 2),
                "repeat_pct": round(rep_pct or 0, 2),
                "mom_total_members": round(mom_total_m, 2),
                "mom_visits": round(mom_visits, 2),
                "mom_new_members": round(mom_new_m, 2),
                "mom_repeat_members": round(mom_repeat_m, 2),
                "db_size": cumulative_new_members
            })

        return data

    def get_retail_loyalty_advanced_report(self, filters):
        """
        Advanced analytics engine built on top of get_retail_loyalty_report().
        Returns enriched data: executive summary, YoY seasonality, anomaly detection,
        per-month risk flags, correlation signals, and retention period analysis.
        """
        raw = self.get_retail_loyalty_report(filters)
        if not raw:
            return {"monthly": [], "summary": {}, "seasonality": {}, "insights": [], "risks": []}

        # ------------------------------------------------------------------ #
        # 1. EXECUTIVE SUMMARY
        # ------------------------------------------------------------------ #
        total_months = len(raw)
        total_new = sum(r['new_members'] for r in raw)
        total_visits_all = sum(r['total_visits'] for r in raw)
        avg_repeat_pct = sum(r['repeat_pct'] for r in raw) / total_months
        avg_engagement = sum(r['engagement_rate'] for r in raw) / total_months

        peak_members_row = max(raw, key=lambda x: x['total_members'])
        low_members_row = min(raw, key=lambda x: x['total_members'])
        peak_repeat_row = max(raw, key=lambda x: x['repeat_pct'])
        peak_visits_row = max(raw, key=lambda x: x['total_visits'])
        low_repeat_row = min(raw, key=lambda x: x['repeat_pct'])

        db_start = raw[0]['db_size'] - raw[0]['new_members']
        db_end = raw[-1]['db_size']
        db_growth_pct = round((db_end - db_start) / db_start * 100, 1) if db_start > 0 else 0

        # Month-over-month velocity: 2nd half avg new members vs 1st half
        half = total_months // 2
        first_half_avg = sum(r['new_members'] for r in raw[:half]) / max(half, 1)
        second_half_avg = sum(r['new_members'] for r in raw[half:]) / max(total_months - half, 1)
        growth_trend = "Accelerating" if second_half_avg > first_half_avg * 1.05 else (
            "Decelerating" if second_half_avg < first_half_avg * 0.95 else "Stable"
        )

        summary = {
            "total_months": total_months,
            "total_new_members": total_new,
            "total_visits": total_visits_all,
            "avg_repeat_pct": round(avg_repeat_pct, 1),
            "avg_engagement_rate": round(avg_engagement, 2),
            "peak_members_month": peak_members_row['month'],
            "peak_members_value": peak_members_row['total_members'],
            "low_members_month": low_members_row['month'],
            "low_members_value": low_members_row['total_members'],
            "peak_repeat_month": peak_repeat_row['month'],
            "peak_repeat_pct": peak_repeat_row['repeat_pct'],
            "low_repeat_month": low_repeat_row['month'],
            "low_repeat_pct": low_repeat_row['repeat_pct'],
            "peak_visits_month": peak_visits_row['month'],
            "peak_visits_value": peak_visits_row['total_visits'],
            "db_start": db_start,
            "db_end": db_end,
            "db_growth_pct": db_growth_pct,
            "growth_trend": growth_trend,
            "avg_monthly_new": round(total_new / total_months, 0),
        }

        # ------------------------------------------------------------------ #
        # 2. PER-MONTH ENRICHMENT: Risk flags, anomaly, rolling avg
        # ------------------------------------------------------------------ #
        # Compute overall averages for benchmarking
        overall_avg_repeat = avg_repeat_pct
        overall_avg_visits = total_visits_all / total_months
        overall_avg_new = total_new / total_months

        # Rolling 3-month repeat % averages
        def rolling_avg(data, key, window=3):
            result = []
            for i in range(len(data)):
                start = max(0, i - window + 1)
                vals = [data[j][key] for j in range(start, i + 1)]
                result.append(round(sum(vals) / len(vals), 2))
            return result

        rolling_repeat = rolling_avg(raw, 'repeat_pct')
        rolling_visits = rolling_avg(raw, 'total_visits')

        enriched_monthly = []
        for i, r in enumerate(raw):
            flags = []
            risk_level = "Normal"

            # Anomaly: high visits but low repeat %
            if r['total_visits'] > overall_avg_visits * 1.15 and r['repeat_pct'] < overall_avg_repeat * 0.85:
                flags.append("High visits, low repeat — weak conversion")
                risk_level = "Warning"

            # Risk: steep MoM decline in repeat %
            if i > 0 and (r['repeat_pct'] - raw[i - 1]['repeat_pct']) < -5:
                flags.append(f"Repeat % dropped {abs(r['repeat_pct'] - raw[i - 1]['repeat_pct']):.1f}pts MoM")
                risk_level = "Critical" if risk_level == "Warning" else "Warning"

            # Opportunity: high new members but very low repeat %
            if r['new_members'] > overall_avg_new * 1.2 and r['repeat_pct'] < overall_avg_repeat * 0.8:
                flags.append("High acquisition, weak onboarding/retention")
                risk_level = "Opportunity"

            # Positive signal: repeat % well above average
            if r['repeat_pct'] > overall_avg_repeat * 1.1 and not flags:
                flags.append("Strong retention month")
                risk_level = "Positive"

            # Festive spike detection (Sep, Oct, Nov, Dec = months 9,10,11,12)
            try:
                year, month_num = r['month'].split('-')
                is_festive = int(month_num) in [9, 10, 11, 12]
            except Exception:
                is_festive = False

            enriched_monthly.append({
                **r,
                "rolling_repeat_avg": rolling_repeat[i],
                "rolling_visits_avg": rolling_visits[i],
                "flags": flags,
                "risk_level": risk_level,
                "is_festive": is_festive,
            })

        # ------------------------------------------------------------------ #
        # 3. RETENTION PERIOD IDENTIFICATION
        #    Find streaks of improving (>=2 consecutive) vs declining repeat %
        # ------------------------------------------------------------------ #
        retention_periods = []
        if len(raw) >= 3:
            i = 1
            while i < len(raw):
                curr_rep = raw[i]['repeat_pct']
                prev_rep = raw[i - 1]['repeat_pct']
                direction = "improving" if curr_rep > prev_rep else "declining"
                streak_start = raw[i - 1]['month']
                streak_count = 1
                j = i + 1
                while j < len(raw):
                    nxt = raw[j]['repeat_pct']
                    prv = raw[j - 1]['repeat_pct']
                    if (nxt > prv) == (direction == "improving"):
                        streak_count += 1
                        j += 1
                    else:
                        break
                if streak_count >= 2:
                    retention_periods.append({
                        "from": streak_start,
                        "to": raw[j - 1]['month'],
                        "direction": direction,
                        "months": streak_count,
                        "start_pct": raw[i - 1]['repeat_pct'],
                        "end_pct": raw[j - 1]['repeat_pct'],
                    })
                i = j

        # ------------------------------------------------------------------ #
        # 4. YoY SEASONALITY: Group same calendar months across years
        # ------------------------------------------------------------------ #
        month_names = {
            '01': 'Jan', '02': 'Feb', '03': 'Mar', '04': 'Apr',
            '05': 'May', '06': 'Jun', '07': 'Jul', '08': 'Aug',
            '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dec'
        }
        seasonality = {}
        for r in raw:
            try:
                year, mnth = r['month'].split('-')
                mon_label = month_names.get(mnth, mnth)
                if mon_label not in seasonality:
                    seasonality[mon_label] = {}
                seasonality[mon_label][year] = {
                    "total_members": r['total_members'],
                    "total_visits": r['total_visits'],
                    "new_members": r['new_members'],
                    "repeat_pct": r['repeat_pct'],
                }
            except Exception:
                pass

        # Sort seasonality by calendar month order
        cal_order = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        seasonality_ordered = {m: seasonality[m] for m in cal_order if m in seasonality}

        # ------------------------------------------------------------------ #
        # 5. AUTO-GENERATED BUSINESS INSIGHTS (human-readable)
        # ------------------------------------------------------------------ #
        insights = []

        # Insight 1: Overall repeat % stability
        stable_months = sum(1 for r in raw if r['repeat_pct'] >= 50)
        if stable_months > total_months * 0.6:
            insights.append({
                "category": "Retention",
                "priority": "Positive",
                "title": "Strong Loyalty Base Established",
                "text": f"Repeat % exceeded 50% in {stable_months} out of {total_months} months, indicating a stable and loyal customer base that forms the backbone of your revenue."
            })
        elif stable_months == 0:
            insights.append({
                "category": "Retention",
                "priority": "Critical",
                "title": "Loyalty Base Needs Strengthening",
                "text": f"Repeat % never exceeded 50% in this period. Average retention was {avg_repeat_pct:.1f}%. Focus on post-purchase engagement programs to convert new buyers into repeat customers."
            })

        # Insight 2: Growth trend
        insights.append({
            "category": "Growth",
            "priority": "High" if growth_trend != "Accelerating" else "Positive",
            "title": f"Membership Growth is {growth_trend}",
            "text": f"Database grew by {db_growth_pct}% over this period. Monthly new member acquisition averaged {int(total_new / total_months):,} members/month. The second half of the period shows {'stronger' if growth_trend == 'Accelerating' else 'weaker'} acquisition momentum compared to the first half."
        })

        # Insight 3: Peak months
        insights.append({
            "category": "Seasonality",
            "priority": "Info",
            "title": f"Peak Activation: {peak_members_row['month']}",
            "text": f"{peak_members_row['month']} was the strongest month with {peak_members_row['total_members']:,} members active and {peak_visits_row['total_visits']:,} visits. Check if this aligns with a festive or promotional campaign that can be replicated."
        })

        # Insight 4: Weak retention months
        weak_retention = [r for r in raw if r['repeat_pct'] < overall_avg_repeat * 0.75]
        if weak_retention:
            names = ", ".join(r['month'] for r in weak_retention[:3])
            insights.append({
                "category": "Risk",
                "priority": "Warning",
                "title": "Months with Weak Retention Identified",
                "text": f"{names} showed repeat % significantly below the {overall_avg_repeat:.1f}% average. High new member inflows without strong repeat conversion suggest gaps in onboarding or post-purchase engagement."
            })

        # Insight 5: Retention improvement streaks
        long_improvements = [p for p in retention_periods if p['direction'] == 'improving' and p['months'] >= 3]
        if long_improvements:
            best = max(long_improvements, key=lambda x: x['months'])
            insights.append({
                "category": "Retention",
                "priority": "Positive",
                "title": f"Retention Improved Consistently: {best['from']} → {best['to']}",
                "text": f"Customer retention improved for {best['months']} consecutive months from {best['from']} to {best['to']}, rising from {best['start_pct']:.1f}% to {best['end_pct']:.1f}%. This suggests a successful loyalty or re-engagement initiative during this window."
            })

        # Insight 6: Anomaly months
        anomalies = [r for r in enriched_monthly if 'High visits, low repeat' in ' '.join(r['flags'])]
        if anomalies:
            anom_names = ", ".join(r['month'] for r in anomalies[:3])
            insights.append({
                "category": "Anomaly",
                "priority": "Warning",
                "title": "Visit Spikes Not Converting to Loyalty",
                "text": f"{anom_names} showed unusually high visit volume but below-average repeat %. These spikes may be driven by promotions or walk-in traffic that isn't being converted into the loyalty database effectively."
            })

        # Insight 7: Festive season pattern
        festive_data = [r for r in raw if r.get('is_festive') or (lambda m: m.split('-')[1] in ['09','10','11','12'] if '-' in m else False)(r['month'])]
        non_festive_data = [r for r in raw if r not in festive_data]
        if festive_data and non_festive_data:
            festive_avg_new = sum(r['new_members'] for r in festive_data) / len(festive_data)
            non_festive_avg_new = sum(r['new_members'] for r in non_festive_data) / len(non_festive_data)
            if festive_avg_new > non_festive_avg_new * 1.1:
                insights.append({
                    "category": "Seasonality",
                    "priority": "Info",
                    "title": "Festive Season Drives Higher Acquisition (Sep–Dec)",
                    "text": f"Sep–Dec months averaged {int(festive_avg_new):,} new members/month vs. {int(non_festive_avg_new):,} in non-festive months — a {((festive_avg_new/non_festive_avg_new - 1)*100):.0f}% uplift. Budget disproportionately for festive season campaigns."
                })

        # ------------------------------------------------------------------ #
        # 6. RISKS & OPPORTUNITIES TABLE
        # ------------------------------------------------------------------ #
        risks = []
        for r in enriched_monthly:
            for flag in r['flags']:
                risks.append({
                    "month": r['month'],
                    "risk_level": r['risk_level'],
                    "flag": flag,
                    "repeat_pct": r['repeat_pct'],
                    "total_visits": r['total_visits'],
                    "new_members": r['new_members'],
                    "recommendation": (
                        "Audit your acquisition source quality — too many low-intent buyers are entering the database without converting to repeat customers."
                        if 'High acquisition' in flag else
                        "Review the promotional calendar for this month — the visit spike suggests a campaign, but loyalty capture was weak."
                        if 'High visits' in flag else
                        "Investigate if there was a negative event (price hike, stock issue, competitor campaign) causing repeat customers to drop off."
                        if 'dropped' in flag.lower() else
                        "This month is performing well — document what drove strong retention and replicate the strategy."
                    )
                })

        return {
            "monthly": enriched_monthly,
            "summary": summary,
            "seasonality": seasonality_ordered,
            "insights": insights,
            "risks": risks,
            "retention_periods": retention_periods,
        }
