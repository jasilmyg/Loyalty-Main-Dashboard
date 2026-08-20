import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection
from django.core.cache import cache
from analytics.clickhouse_service import ch_query

def sync_monthly_retention():
    print("\n--- Syncing Monthly Retention ---")
    query = """
        WITH baseline AS (
            SELECT DISTINCT customer_mobile
            FROM azure_invoice_report
            WHERE toDate(date) < '2026-01-01'
              AND toDate(date) != '1970-01-01'
              AND invoice_total > 0
              AND length(customer_mobile) = 10
              AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
              AND customer_mobile != ''
        ),
        purchases_2026 AS (
            SELECT 
                customer_mobile, 
                toStartOfMonth(toDate(date)) AS month_start, 
                invoice_total
            FROM azure_invoice_report
            WHERE toDate(date) >= '2026-01-01'
              AND invoice_total > 0
              AND length(customer_mobile) = 10
              AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
              AND customer_mobile != ''
              AND customer_mobile IN (SELECT customer_mobile FROM baseline)
        ),
        first_month AS (
            SELECT 
                customer_mobile, 
                min(month_start) AS first_month_2026
            FROM purchases_2026
            GROUP BY customer_mobile
        )
        SELECT
            formatDateTime(f.first_month_2026, '%b %Y') AS month_label,
            f.first_month_2026 AS month_start,
            count(DISTINCT f.customer_mobile) AS unique_customers,
            round(sum(p.invoice_total), 2) AS total_sales
        FROM first_month f
        JOIN purchases_2026 p 
          ON p.customer_mobile = f.customer_mobile 
         AND p.month_start = f.first_month_2026
        GROUP BY f.first_month_2026
        ORDER BY f.first_month_2026 ASC
    """
    
    t0 = time.time()
    rows = ch_query(query)
    print(f"ClickHouse query finished in {time.time()-t0:.2f}s. Rows: {len(rows)}")
    
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS mv_monthly_retention_2026 CASCADE;")
        
        cur.execute("""
            CREATE TABLE mv_monthly_retention_2026 (
                month_label TEXT,
                month_start DATE,
                unique_customers INTEGER,
                total_sales FLOAT
            )
        """)
        
        for r in rows:
            cur.execute("INSERT INTO mv_monthly_retention_2026 VALUES (%s, %s, %s, %s)", r)
            
        cur.execute("CREATE UNIQUE INDEX idx_mv_mr_2026 ON mv_monthly_retention_2026 (month_start);")
    print("Postgres table mv_monthly_retention_2026 populated successfully.")


def sync_yearly_cohorts():
    print("\n--- Syncing Yearly Cohorts ---")
    query = """
        WITH base AS (
            SELECT customer_mobile AS mobile,
                   toDate(date)    AS sale_d,
                   invoice_total   AS revenue
            FROM azure_invoice_report
            WHERE toDate(date) != toDate('1970-01-01')
              AND invoice_total > 0
              AND length(customer_mobile) = 10
              AND customer_mobile != ''
              AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
        ),
        customer_first_visit AS (
            SELECT mobile,
                   toString(toYear(min(sale_d))) AS cohort_year,
                   min(sale_d) AS first_date
            FROM base
            GROUP BY mobile
        ),
        customer_activities AS (
            SELECT b.mobile, b.sale_d AS activity_date,
                   b.revenue, f.first_date,
                   f.cohort_year,
                   toYear(b.sale_d) - toYear(f.first_date) AS year_index
            FROM base b
            JOIN customer_first_visit f ON b.mobile = f.mobile
        ),
        cohort_yearly_stats AS (
            SELECT cohort_year, year_index,
                   count(DISTINCT mobile) AS active_customers,
                   sum(revenue)           AS year_revenue
            FROM customer_activities
            GROUP BY cohort_year, year_index
        ),
        cohort_base_size AS (
            SELECT cohort_year, active_customers AS initial_size
            FROM cohort_yearly_stats
            WHERE year_index = 0
        ),
        cohort_otb AS (
            SELECT cohort_year, count(DISTINCT mobile) AS one_time_buyers
            FROM (
                SELECT mobile, cohort_year, count(DISTINCT activity_date) AS lv
                FROM customer_activities
                GROUP BY mobile, cohort_year
            )
            WHERE lv = 1
            GROUP BY cohort_year
        ),
        cohort_nrp AS (
            SELECT cohort_year, count(DISTINCT mobile) AS no_return_purchases
            FROM (
                SELECT mobile, cohort_year, max(year_index) AS myi
                FROM customer_activities
                GROUP BY mobile, cohort_year
            )
            WHERE myi = 0
            GROUP BY cohort_year
        )
        SELECT s.cohort_year, s.year_index, s.active_customers, s.year_revenue,
               b.initial_size,
               if(b.initial_size > 0, s.active_customers * 100.0 / b.initial_size, 0) AS retention_rate,
               coalesce(o.one_time_buyers, 0) AS one_time_buyers, 
               coalesce(n.no_return_purchases, 0) AS no_return_purchases
        FROM cohort_yearly_stats s
        JOIN cohort_base_size b ON s.cohort_year = b.cohort_year
        LEFT JOIN cohort_otb o ON s.cohort_year = o.cohort_year
        LEFT JOIN cohort_nrp n ON s.cohort_year = n.cohort_year
        ORDER BY s.cohort_year DESC, s.year_index ASC
    """
    
    t0 = time.time()
    rows = ch_query(query)
    print(f"ClickHouse query finished in {time.time()-t0:.2f}s. Rows: {len(rows)}")
    
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS mv_yearly_cohort CASCADE;")
        
        cur.execute("""
            CREATE TABLE mv_yearly_cohort (
                cohort_year TEXT,
                year_index INTEGER,
                active_customers BIGINT,
                year_revenue FLOAT,
                initial_size BIGINT,
                retention_rate NUMERIC,
                one_time_buyers BIGINT,
                no_return_purchases BIGINT
            )
        """)
        
        for r in rows:
            cur.execute("INSERT INTO mv_yearly_cohort VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", r)
            
        cur.execute("CREATE UNIQUE INDEX idx_mv_yearly_cohort ON mv_yearly_cohort(cohort_year, year_index);")
    print("Postgres table mv_yearly_cohort populated successfully.")


if __name__ == '__main__':
    print("=" * 60)
    print(" Syncing Cohort MVs from ClickHouse `azure_invoice_report`")
    print("=" * 60)
    
    sync_monthly_retention()
    sync_yearly_cohorts()
    
    print("\n--- Clearing Caches ---")
    cache.delete('v3_azure_yearly_cohort_global')
    cache.delete('cohort_retention_global')
    cache.delete('monthly_retention_global')
    print("Django caches cleared successfully.")
    print("\nDONE!")
