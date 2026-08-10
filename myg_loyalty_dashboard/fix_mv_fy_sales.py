"""
Create mv_fy_sales from mv_fy_sales_branch (already aggregated - very fast!)
"""
import psycopg2, time

HOST = 'db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com'
USER = 'doadmin'
PASS = '***' # Removed secret for commit
DB   = 'defaultdb'

conn = psycopg2.connect(host=HOST, user=USER, password=PASS, database=DB, port=25060, sslmode='require', connect_timeout=10)
conn.autocommit = True

def run(sql, label=""):
    t = time.time()
    with conn.cursor() as cur:
        cur.execute(sql)
        if label:
            print(f"  OK: {label} ({(time.time()-t)*1000:.0f}ms)")

print("Creating mv_fy_sales from mv_fy_sales_branch...")

# Drop if exists
run("DROP MATERIALIZED VIEW IF EXISTS mv_fy_sales CASCADE;", "drop old mv_fy_sales")

# Create by aggregating mv_fy_sales_branch (super fast - 893 rows)
run("""
CREATE MATERIALIZED VIEW mv_fy_sales AS
    SELECT
        fy_year,
        SUM(total_sale)      AS total_sale,
        SUM(total_customers) AS total_customers,
        SUM(new_sale)        AS new_sale
    FROM mv_fy_sales_branch
    GROUP BY fy_year
    ORDER BY fy_year ASC;
""", "create mv_fy_sales")

# Add unique index for fast lookups
run("CREATE UNIQUE INDEX ON mv_fy_sales(fy_year);", "create index")

# Verify
with conn.cursor() as cur:
    cur.execute("SELECT fy_year, total_sale, total_customers, new_sale FROM mv_fy_sales ORDER BY fy_year;")
    rows = cur.fetchall()
    print(f"\nVerification - mv_fy_sales has {len(rows)} rows:")
    for r in rows:
        print(f"  FY {r[0]}: sale={r[1]:,.0f}, customers={r[2]:,}, new={r[3]:,.0f}")

conn.close()
print("\nDone! mv_fy_sales is ready.")
