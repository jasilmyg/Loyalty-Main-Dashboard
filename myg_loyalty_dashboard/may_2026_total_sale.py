import os
import psycopg2

PG_CONFIG = dict(
    host     = "db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com",
    port     = 25060,
    dbname   = "defaultdb",
    user     = "doadmin",
    password = os.environ.get('PGPASSWORD', ''),
    sslmode  = "require",
    connect_timeout = 30,
)

conn = psycopg2.connect(**PG_CONFIG)
cur  = conn.cursor()

cur.execute("""
    SELECT 
        COUNT(*) AS total_invoices,
        COUNT(DISTINCT "Customer Mobile") AS unique_customers,
        ROUND(SUM(CAST(NULLIF(REPLACE(REPLACE(CAST("Total Value" AS TEXT), ',', ''), chr(8377), ''), '') AS NUMERIC)), 2) AS total_sale
    FROM sales_data
    WHERE parsed_date >= '2026-05-01' AND parsed_date < '2026-06-01'
""")
row = cur.fetchone()

print("==================================================")
print("  MAY 2026 TOTAL SALES SUMMARY")
print("==================================================")
print(f"  Total Invoices    : {row[0]:,}")
print(f"  Unique Customers  : {row[1]:,}")
print(f"  Total Sale Amount : Rs. {row[2]:,.2f}" if row[2] else "  Total Sale Amount : NULL")
print("==================================================")

cur.close()
conn.close()
