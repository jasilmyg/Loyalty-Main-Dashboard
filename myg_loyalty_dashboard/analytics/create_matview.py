"""
Create a materialized view for monthly summary — refreshed periodically.
This makes the Sales Overview load in under 1 second.
"""
import psycopg2, time, os

conn = psycopg2.connect(
    host=os.environ.get('PGHOST', 'localhost'),
    port=int(os.environ.get('PGPORT', 25060)),
    dbname=os.environ.get('PGDATABASE', 'defaultdb'),
    user=os.environ.get('PGUSER', 'doadmin'),
    password=os.environ.get('PGPASSWORD', ''),
    sslmode='require', connect_timeout=10
)
conn.autocommit = True
cur = conn.cursor()

print("Creating monthly summary materialized view...")
cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_summary;")
cur.execute("""
    CREATE MATERIALIZED VIEW mv_monthly_summary AS
    WITH parsed AS (
        SELECT
            "Invoice Number", "Branch", "Staff", "RBM", "BDM",
            "Customer Mobile",
            "Total Value"::FLOAT AS val,
            CASE
                WHEN "Date" ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN TO_DATE(SUBSTRING("Date", 1, 10), 'YYYY-MM-DD')
                WHEN "Date" ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}' THEN TO_DATE("Date", 'DD-MM-YYYY')
                ELSE NULL
            END AS parsed_date
        FROM sales_data
        WHERE "Date" IS NOT NULL AND "Date" != '' AND "Total Value" IS NOT NULL
    )
    SELECT
        DATE_TRUNC('month', parsed_date)  AS month_date,
        "Branch",
        "Staff",
        "RBM",
        "BDM",
        SUM(val)                          AS revenue,
        COUNT(DISTINCT "Invoice Number")  AS invoices,
        COUNT(DISTINCT "Customer Mobile") AS customers
    FROM parsed
    WHERE parsed_date IS NOT NULL
    GROUP BY DATE_TRUNC('month', parsed_date), "Branch", "Staff", "RBM", "BDM";
""")
print("Creating index on mv_monthly_summary...")
cur.execute("CREATE INDEX ON mv_monthly_summary (month_date);")
cur.execute("CREATE INDEX ON mv_monthly_summary (\"Branch\");")
print("Materialized view created!")

# Test speed
print("\nTesting query speed on materialized view...")
t = time.time()
cur.execute("""
    SELECT SUM(revenue), SUM(invoices)
    FROM mv_monthly_summary
""")
print(f"Total SUM from mv_monthly_summary: {cur.fetchone()} in {time.time()-t:.3f}s")

t = time.time()
cur.execute("""
    SELECT TO_CHAR(month_date, 'Mon YY'), SUM(revenue)
    FROM mv_monthly_summary
    GROUP BY month_date
    ORDER BY month_date ASC
""")
rows = cur.fetchall()
print(f"Monthly trend from mv_monthly_summary: {len(rows)} rows in {time.time()-t:.3f}s")

cur.close()
conn.close()
print("\nDone!")
