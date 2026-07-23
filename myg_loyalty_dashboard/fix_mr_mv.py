import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
import psycopg2
from django.conf import settings
from analytics.services import TABLE

db = settings.DATABASES['default']
conn = psycopg2.connect(
    host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
    user=db['USER'], password=db['PASSWORD'], sslmode='require'
)
conn.autocommit = True
cur = conn.cursor()

print("Step 1: Drop old view...")
cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_retention_2026 CASCADE;")
print("  OK")

print("Step 2: Create mv_monthly_retention_2026...")
t0 = time.time()
sql = """
CREATE MATERIALIZED VIEW mv_monthly_retention_2026 AS
WITH base AS (
    SELECT mobile
    FROM mv_customer_summary
    WHERE first_visit <= '2025-12-31'
),
p2026 AS (
    SELECT
        s."Customer Mobile",
        DATE_TRUNC('month', s.parsed_date) AS ms,
        s."Total Value"
    FROM sales_data s
    JOIN base b ON b.mobile = s."Customer Mobile"
    WHERE s.parsed_date >= '2026-01-01'
      AND LENGTH(s."Customer Mobile") = 10
      AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
),
fm AS (
    SELECT "Customer Mobile", MIN(ms) AS fms
    FROM p2026
    GROUP BY "Customer Mobile"
),
agg AS (
    SELECT
        f.fms AS ms,
        COUNT(DISTINCT f."Customer Mobile")::INT AS unique_customers,
        SUM(p."Total Value")::FLOAT AS total_sales
    FROM fm f
    JOIN p2026 p ON p."Customer Mobile" = f."Customer Mobile" AND p.ms = f.fms
    GROUP BY f.fms
)
SELECT
    TO_CHAR(ms, 'Mon YYYY') AS month_label,
    ms AS month_start,
    unique_customers,
    ROUND(total_sales::NUMERIC, 2) AS total_sales
FROM agg
ORDER BY ms;
"""
cur.execute(sql)
print(f"  OK ({time.time()-t0:.1f}s)")

print("Step 3: Create unique index...")
cur.execute("CREATE UNIQUE INDEX ON mv_monthly_retention_2026(month_start);")
print("  OK")

print("Step 4: Verify...")
cur.execute("SELECT month_label, unique_customers, total_sales FROM mv_monthly_retention_2026 ORDER BY month_start;")
rows = cur.fetchall()
print(f"  {len(rows)} months:")
for r in rows:
    print(f"    {r[0]:12s}  customers={r[1]:,}  sales={r[2]:,.0f}")

conn.close()
print("\nDone! mv_monthly_retention_2026 is ready.")
