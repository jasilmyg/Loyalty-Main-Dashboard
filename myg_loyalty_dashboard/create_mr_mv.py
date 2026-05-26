import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
os.environ['PGDATABASE'] = 'defaultdb'
os.environ['PGUSER'] = 'doadmin'
os.environ['PGPASSWORD'] = 'YOUR_DB_PASSWORD'
os.environ['PGHOST'] = 'db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com'
os.environ['PGPORT'] = '25060'
django.setup()

import psycopg2
from analytics.services import TABLE

conn = psycopg2.connect(
    host=os.environ['PGHOST'],
    port=os.environ['PGPORT'],
    dbname=os.environ['PGDATABASE'],
    user=os.environ['PGUSER'],
    password=os.environ['PGPASSWORD'],
    sslmode='require'
)
conn.autocommit = True
cur = conn.cursor()

print("Creating mv_monthly_retention_2026...")
try:
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_retention_2026 CASCADE;")
    cur.execute(f"""
        CREATE MATERIALIZED VIEW mv_monthly_retention_2026 AS
        WITH
        -- Step 1: baseline = customers who purchased on or before 2025-12-31
        base_customers AS (
            SELECT mobile AS "Customer Mobile"
            FROM mv_customer_summary
            WHERE first_visit <= '2025-12-31'
        ),
        -- Step 2: 2026 purchases
        purchases_2026 AS (
            SELECT
                s."Customer Mobile",
                DATE_TRUNC('month', s."Date") AS month_start,
                s."Total Value"
            FROM {TABLE} s
            INNER JOIN base_customers b
                ON b."Customer Mobile" = s."Customer Mobile"
            WHERE s."Date" >= '2026-01-01'
              AND s."Customer Mobile" IS NOT NULL
              AND LENGTH(s."Customer Mobile") = 10
              AND s."Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
        ),
        -- Step 3: first month in 2026
        first_month AS (
            SELECT
                "Customer Mobile",
                MIN(month_start) AS first_month_2026
            FROM purchases_2026
            GROUP BY "Customer Mobile"
        ),
        -- Step 4: aggregate
        monthly_agg AS (
            SELECT
                f.first_month_2026                        AS month_start,
                COUNT(DISTINCT f."Customer Mobile")::INT  AS unique_customers,
                SUM(p."Total Value")::FLOAT               AS total_sales
            FROM first_month f
            JOIN purchases_2026 p
              ON p."Customer Mobile" = f."Customer Mobile"
             AND p.month_start        = f.first_month_2026
            GROUP BY f.first_month_2026
        )
        SELECT
            TO_CHAR(month_start, 'Mon YYYY')  AS month_label,
            month_start,
            unique_customers,
            ROUND(total_sales::NUMERIC, 2)    AS total_sales
        FROM monthly_agg
        ORDER BY month_start ASC;
    """)
    print("Done creating materialized view.")
    cur.execute("CREATE UNIQUE INDEX idx_mv_mr_2026 ON mv_monthly_retention_2026 (month_start);")
    print("Index created.")
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    cur.close()
    conn.close()
