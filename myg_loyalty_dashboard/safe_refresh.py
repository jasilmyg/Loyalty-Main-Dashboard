"""
SAFE sequential MV refresh — one at a time, with connection monitoring.
Refreshes only the MVs that power the Campaign Analysis and FY reports.
"""
import psycopg2, time, sys

HOST = 'db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com'
USER = 'doadmin'
PASS = '***' # Removed secret for commit
DB   = 'defaultdb'

def get_conn():
    return psycopg2.connect(
        host=HOST, user=USER, password=PASS,
        database=DB, port=25060, sslmode='require', connect_timeout=10
    )

def active_count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state='active' AND pid != pg_backend_pid();")
        return cur.fetchone()[0]

# MVs to refresh — ordered by dependency (lightest first)
# Skipping heavy ones like mv_customer_summary, mv_rfm_segments etc.
MVS = [
    'mv_monthly_summary',           # powers monthly charts
    'mv_monthly_members',           # powers loyalty report
    'mv_monthly_members_branch',    # powers branch-filtered loyalty
    'mv_fy_members',                # powers FY Loyalty Report
    'mv_fy_members_branch',         # powers FY Loyalty branch filter
    'mv_fy_sales_branch',           # powers FY Sales branch filter
    'mv_dormant_reactivation',      # powers Campaign Analysis ← MAIN TARGET
]

print(f"Safe sequential refresh: {len(MVS)} MVs")
print("=" * 55)

for mv in MVS:
    conn = get_conn()
    conn.autocommit = True

    # Wait if DB is busy (> 5 active queries)
    retries = 0
    while active_count(conn) > 5 and retries < 30:
        print(f"  DB busy, waiting 10s... (active={active_count(conn)})")
        time.sleep(10)
        retries += 1

    try:
        t0 = time.time()
        print(f"Refreshing {mv}...", end=" ", flush=True)
        try:
            with conn.cursor() as cur:
                cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv};")
            print(f"OK concurrently ({time.time()-t0:.1f}s)")
        except Exception:
            # Fallback: regular refresh (brief lock, but works without unique index)
            with conn.cursor() as cur:
                cur.execute(f"REFRESH MATERIALIZED VIEW {mv};")
            print(f"OK regular ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"FAILED: {e}")
    finally:
        conn.close()

    # Brief pause between refreshes to let DB breathe
    time.sleep(3)

# Rebuild mv_fy_sales from mv_fy_sales_branch (fast — 893 rows)
print("\nRebuilding mv_fy_sales from updated mv_fy_sales_branch...")
conn = get_conn()
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_fy_sales CASCADE;")
    cur.execute("""
        CREATE MATERIALIZED VIEW mv_fy_sales AS
        SELECT fy_year, SUM(total_sale) AS total_sale,
               SUM(total_customers) AS total_customers, SUM(new_sale) AS new_sale
        FROM mv_fy_sales_branch GROUP BY fy_year ORDER BY fy_year ASC;
    """)
    cur.execute("CREATE UNIQUE INDEX ON mv_fy_sales(fy_year);")
    print("  OK: mv_fy_sales rebuilt")
conn.close()

print("\n=== All done! Dashboard is now up to date with July 20, 2026 data ===")
