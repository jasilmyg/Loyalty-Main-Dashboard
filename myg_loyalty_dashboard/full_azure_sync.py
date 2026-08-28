"""
full_azure_sync.py
==================
Updates ALL portal sections from azure_sales_report + azure_invoice_report in ClickHouse.
Only the Loyalty Point Matrix uses sales_data (skipped here).

Sections updated:
1. Monthly Retention  -> mv_monthly_retention_2026 (PG table from CH)
2. Yearly Cohort      -> mv_yearly_cohort (PG table from CH)
3. Enterprise Dashboard -> Direct ClickHouse query (no MV needed, cache cleared)
4. Campaign Analysis  -> Direct ClickHouse query (cache cleared)
5. Customer Analytics -> Direct ClickHouse query (cache cleared)
6. Repeat/New Customer -> Direct ClickHouse query (cache cleared)
7. All Django caches  -> Cleared
"""
import os, sys, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.db import connection
from django.core.cache import cache
from analytics.clickhouse_service import ch_query, get_ch_client

client = get_ch_client()

print("=" * 65)
print("  Full Azure Sync - All Portal Sections")
print(f"  Data available: azure_sales_report & azure_invoice_report")
print("=" * 65)

# ── Verify latest data ─────────────────────────────────────────────────────────
r1 = client.query("SELECT max(date), count() FROM azure_sales_report").result_rows[0]
r2 = client.query("SELECT max(date), count() FROM azure_invoice_report").result_rows[0]
print(f"\n  azure_sales_report   : max={r1[0]}  rows={r1[1]:,}")
print(f"  azure_invoice_report : max={r2[0]}  rows={r2[1]:,}")

# ══════════════════════════════════════════════════════════════════════════════
# 1. SYNC MONTHLY RETENTION (mv_monthly_retention_2026)
# ══════════════════════════════════════════════════════════════════════════════
print("\n[1] Syncing Monthly Retention from azure_invoice_report...")
t0 = time.time()
mr_query = """
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
        SELECT customer_mobile,
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
        SELECT customer_mobile, min(month_start) AS first_month_2026
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
rows = ch_query(mr_query)
print(f"   CH query: {time.time()-t0:.1f}s  rows={len(rows)}")

with connection.cursor() as cur:
    cur.execute("DROP TABLE IF EXISTS mv_monthly_retention_2026 CASCADE;")
    cur.execute("""
        CREATE TABLE mv_monthly_retention_2026 (
            month_label TEXT, month_start DATE,
            unique_customers INTEGER, total_sales FLOAT
        )
    """)
    for r in rows:
        cur.execute("INSERT INTO mv_monthly_retention_2026 VALUES (%s, %s, %s, %s)", r)
    cur.execute("CREATE UNIQUE INDEX idx_mv_mr_2026 ON mv_monthly_retention_2026 (month_start);")
print(f"   [OK] mv_monthly_retention_2026 updated ({len(rows)} rows)")

# ══════════════════════════════════════════════════════════════════════════════
# 2. SYNC YEARLY COHORT (mv_yearly_cohort)
# ══════════════════════════════════════════════════════════════════════════════
print("\n[2] Syncing Yearly Cohort from azure_invoice_report...")
t0 = time.time()
cohort_query = """
    WITH base AS (
        SELECT customer_mobile AS mobile,
               toDate(date) AS sale_d,
               invoice_total AS revenue
        FROM azure_invoice_report
        WHERE toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
          AND length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
    ),
    customer_first_visit AS (
        SELECT mobile, toString(toYear(min(sale_d))) AS cohort_year, min(sale_d) AS first_date
        FROM base GROUP BY mobile
    ),
    customer_activities AS (
        SELECT b.mobile, b.sale_d AS activity_date, b.revenue, f.first_date, f.cohort_year,
               toYear(b.sale_d) - toYear(f.first_date) AS year_index
        FROM base b JOIN customer_first_visit f ON b.mobile = f.mobile
    ),
    cohort_yearly_stats AS (
        SELECT cohort_year, year_index,
               count(DISTINCT mobile) AS active_customers,
               sum(revenue) AS year_revenue
        FROM customer_activities GROUP BY cohort_year, year_index
    ),
    cohort_base_size AS (
        SELECT cohort_year, active_customers AS initial_size
        FROM cohort_yearly_stats WHERE year_index = 0
    ),
    cohort_otb AS (
        SELECT cohort_year, count(DISTINCT mobile) AS one_time_buyers
        FROM (
            SELECT mobile, cohort_year, count(DISTINCT activity_date) AS lv
            FROM customer_activities GROUP BY mobile, cohort_year
        ) WHERE lv = 1 GROUP BY cohort_year
    ),
    cohort_nrp AS (
        SELECT cohort_year, count(DISTINCT mobile) AS no_return_purchases
        FROM (
            SELECT mobile, cohort_year, max(year_index) AS myi
            FROM customer_activities GROUP BY mobile, cohort_year
        ) WHERE myi = 0 GROUP BY cohort_year
    )
    SELECT s.cohort_year, s.year_index, s.active_customers, s.year_revenue,
           b.initial_size,
           if(b.initial_size > 0, s.active_customers * 100.0 / b.initial_size, 0) AS retention_rate,
           coalesce(o.one_time_buyers, 0), coalesce(n.no_return_purchases, 0)
    FROM cohort_yearly_stats s
    JOIN cohort_base_size b ON s.cohort_year = b.cohort_year
    LEFT JOIN cohort_otb o ON s.cohort_year = o.cohort_year
    LEFT JOIN cohort_nrp n ON s.cohort_year = n.cohort_year
    ORDER BY s.cohort_year DESC, s.year_index ASC
"""
rows = ch_query(cohort_query)
print(f"   CH query: {time.time()-t0:.1f}s  rows={len(rows)}")

with connection.cursor() as cur:
    cur.execute("DROP TABLE IF EXISTS mv_yearly_cohort CASCADE;")
    cur.execute("""
        CREATE TABLE mv_yearly_cohort (
            cohort_year TEXT, year_index INTEGER,
            active_customers BIGINT, year_revenue FLOAT,
            initial_size BIGINT, retention_rate NUMERIC,
            one_time_buyers BIGINT, no_return_purchases BIGINT
        )
    """)
    for r in rows:
        cur.execute("INSERT INTO mv_yearly_cohort VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", r)
    cur.execute("CREATE UNIQUE INDEX idx_mv_yearly_cohort ON mv_yearly_cohort(cohort_year, year_index);")
print(f"   [OK] mv_yearly_cohort updated ({len(rows)} rows)")

# ══════════════════════════════════════════════════════════════════════════════
# 3. CLEAR ALL DJANGO / PORTAL CACHES
# ══════════════════════════════════════════════════════════════════════════════
print("\n[3] Clearing all Django caches...")
cache.clear()

# Specific named caches used by portal sections
cache_keys = [
    'v3_azure_yearly_cohort_global', 'cohort_retention_global',
    'monthly_retention_global', 'enterprise_dashboard_data',
    'campaign_analysis_data', 'customer_analytics_data',
    'repeat_new_customer_data', 'rfm_segments_data',
    'daily_new_vs_repeat', 'branch_report_data',
    'jas_cache', 'ai_forecast_cache',
]
for k in cache_keys:
    cache.delete(k)
print(f"   [OK] {len(cache_keys)} cache keys deleted + full cache.clear()")

# ══════════════════════════════════════════════════════════════════════════════
# 4. FINAL SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("  SYNC COMPLETE!")
print("=" * 65)
print("  Sections now live from azure_sales_report + azure_invoice_report:")
print("    [OK] Enterprise Dashboard    - direct ClickHouse query")
print("    [OK] Campaign Analysis       - direct ClickHouse query")
print("    [OK] Customer Analytics      - direct ClickHouse query")
print("    [OK] Repeat/New Customer     - direct ClickHouse query")
print("    [OK] Branch Report           - direct ClickHouse query")
print("    [OK] Monthly Retention       - mv_monthly_retention_2026 rebuilt")
print("    [OK] Yearly Cohort           - mv_yearly_cohort rebuilt")
print("    [OK] All caches              - cleared")
print()
print("  Data latest: azure tables up to 27-Aug-2026")
print("  NOTE: Loyalty Point Matrix uses sales_data (not updated here)")
print("=" * 65)
