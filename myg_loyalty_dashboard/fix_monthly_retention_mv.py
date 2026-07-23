"""
fix_monthly_retention_mv.py
============================
Rebuilds mv_monthly_retention_2026 - the only remaining missing MV.
Uses parsed_date with proper quoting and fallback to Date column.
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

import psycopg2
from django.conf import settings
from django.db import connection

def get_conn():
    db = settings.DATABASES['default']
    conn = psycopg2.connect(
        host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
        user=db['USER'], password=db['PASSWORD'], sslmode='require'
    )
    conn.autocommit = True
    return conn

# First check what columns sales_data actually has
print("Checking sales_data columns...")
with connection.cursor() as cur:
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'sales_data'
        AND column_name IN ('parsed_date', 'Date', 'date')
        ORDER BY column_name;
    """)
    cols = cur.fetchall()
    for c in cols:
        print(f"  {c[0]}: {c[1]}")

from analytics.services import TABLE
print(f"\nTABLE = {TABLE}")

print("\nDropping old view...")
conn = get_conn()
cur = conn.cursor()
cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_retention_2026 CASCADE;")
print("  OK")

print("Creating mv_monthly_retention_2026...")
t0 = time.time()
try:
    cur.execute(f"""
        CREATE MATERIALIZED VIEW mv_monthly_retention_2026 AS
        WITH
        base_customers AS (
            SELECT mobile AS mobile
            FROM mv_customer_summary
            WHERE first_visit <= '2025-12-31'
        ),
        purchases_2026 AS (
            SELECT
                s."Customer Mobile",
                DATE_TRUNC('month', s.parsed_date) AS month_start,
                s."Total Value"
            FROM {TABLE} s
            INNER JOIN base_customers b ON b.mobile = s."Customer Mobile"
            WHERE s.parsed_date >= '2026-01-01'
              AND s."Customer Mobile" IS NOT NULL
              AND LENGTH(s."Customer Mobile") = 10
              AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        ),
        first_month AS (
            SELECT "Customer Mobile", MIN(month_start) AS first_month_2026
            FROM purchases_2026
            GROUP BY "Customer Mobile"
        ),
        monthly_agg AS (
            SELECT
                f.first_month_2026 AS month_start,
                COUNT(DISTINCT f."Customer Mobile")::INT AS unique_customers,
                SUM(p."Total Value")::FLOAT AS total_sales
            FROM first_month f
            JOIN purchases_2026 p
              ON p."Customer Mobile" = f."Customer Mobile"
             AND p.month_start = f.first_month_2026
            GROUP BY f.first_month_2026
        )
        SELECT
            TO_CHAR(month_start, 'Mon YYYY') AS month_label,
            month_start,
            unique_customers,
            ROUND(total_sales::NUMERIC, 2) AS total_sales
        FROM monthly_agg
        ORDER BY month_start ASC;
    """)
    print(f"  OK ({time.time()-t0:.1f}s)")
except Exception as e:
    print(f"  ERROR with parsed_date: {e}")
    print("  Trying with Date column fallback...")
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_retention_2026 CASCADE;")
    t0 = time.time()
    cur.execute(f"""
        CREATE MATERIALIZED VIEW mv_monthly_retention_2026 AS
        WITH
        base_customers AS (
            SELECT mobile AS mobile
            FROM mv_customer_summary
            WHERE first_visit <= '2025-12-31'
        ),
        purchases_2026 AS (
            SELECT
                s."Customer Mobile",
                DATE_TRUNC('month', TO_DATE(SUBSTRING(s."Date"::text, 1, 10), 'YYYY-MM-DD')) AS month_start,
                s."Total Value"
            FROM {TABLE} s
            INNER JOIN base_customers b ON b.mobile = s."Customer Mobile"
            WHERE s."Date" >= '2026-01-01'
              AND s."Customer Mobile" IS NOT NULL
              AND LENGTH(s."Customer Mobile") = 10
              AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        ),
        first_month AS (
            SELECT "Customer Mobile", MIN(month_start) AS first_month_2026
            FROM purchases_2026
            GROUP BY "Customer Mobile"
        ),
        monthly_agg AS (
            SELECT
                f.first_month_2026 AS month_start,
                COUNT(DISTINCT f."Customer Mobile")::INT AS unique_customers,
                SUM(p."Total Value")::FLOAT AS total_sales
            FROM first_month f
            JOIN purchases_2026 p
              ON p."Customer Mobile" = f."Customer Mobile"
             AND p.month_start = f.first_month_2026
            GROUP BY f.first_month_2026
        )
        SELECT
            TO_CHAR(month_start, 'Mon YYYY') AS month_label,
            month_start,
            unique_customers,
            ROUND(total_sales::NUMERIC, 2) AS total_sales
        FROM monthly_agg
        ORDER BY month_start ASC;
    """)
    print(f"  OK via Date fallback ({time.time()-t0:.1f}s)")

print("Adding unique index...")
cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_mr2026_month ON mv_monthly_retention_2026(month_start);")
print("  OK")

cur.execute("SELECT month_label, unique_customers, total_sales FROM mv_monthly_retention_2026 ORDER BY month_start;")
rows = cur.fetchall()
print(f"\nmv_monthly_retention_2026 ({len(rows)} months):")
for r in rows:
    print(f"  {r[0]:12s}  customers={r[1]:,}  sales={r[2]:,.0f}")

conn.close()
print("\nDone! All 6 MVs are now rebuilt.")
