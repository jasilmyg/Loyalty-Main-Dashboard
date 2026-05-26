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

print("Creating mv_dormant_reactivation...")
try:
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_dormant_reactivation CASCADE;")
    cur.execute(f"""
        CREATE MATERIALIZED VIEW mv_dormant_reactivation AS
        WITH customer_history AS (
            SELECT 
                "Customer Mobile",
                MAX(EXTRACT(YEAR FROM "Date")) FILTER (WHERE "Date" < '2026-01-01') AS cohort_year,
                MIN(DATE_TRUNC('month', "Date")) FILTER (WHERE "Date" >= '2026-01-01') AS first_2026_month,
                SUM("Total Value"::numeric) FILTER (WHERE "Date" >= '2026-01-01') AS reactivated_revenue
            FROM {TABLE}
            WHERE "Customer Mobile" IS NOT NULL
              AND LENGTH("Customer Mobile") = 10
              AND "Customer Mobile" NOT IN ('1313131313','0000000000','9999999999')
            GROUP BY "Customer Mobile"
        )
        SELECT 
            cohort_year,
            first_2026_month,
            COUNT(*) AS unique_customers,
            SUM(reactivated_revenue) AS total_revenue
        FROM customer_history
        WHERE cohort_year BETWEEN 2020 AND 2024
        GROUP BY cohort_year, first_2026_month
        ORDER BY cohort_year ASC, first_2026_month ASC;
    """)
    print("Done creating materialized view.")
    cur.execute("CREATE INDEX idx_mv_dormant_cohort ON mv_dormant_reactivation (cohort_year);")
    print("Index created.")
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    cur.close()
    conn.close()
