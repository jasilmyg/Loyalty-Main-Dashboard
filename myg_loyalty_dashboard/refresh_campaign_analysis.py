"""
Refresh ONLY mv_dormant_reactivation and mv_branch_resurrection_2024_2026
"""
import psycopg2, time

HOST = 'db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com'
USER = 'doadmin'
PASS = '***' # Removed secret for commit
DB   = 'defaultdb'

def get_conn():
    return psycopg2.connect(
        host=HOST, user=USER, password=PASS,
        database=DB, port=25060, sslmode='require', connect_timeout=10
    )

# Check columns first
conn = get_conn()
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='mv_dormant_reactivation' ORDER BY ordinal_position;")
    cols = [r[0] for r in cur.fetchall()]
    print(f"mv_dormant_reactivation columns: {cols}")

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='mv_branch_resurrection_2024_2026' ORDER BY ordinal_position;")
    cols2 = [r[0] for r in cur.fetchall()]
    print(f"mv_branch_resurrection_2024_2026 columns: {cols2}")

    cur.execute("SELECT * FROM mv_dormant_reactivation LIMIT 3;")
    print(f"\nSample rows:")
    for r in cur.fetchall():
        print(f"  {r}")
conn.close()

time.sleep(2)

# Refresh mv_dormant_reactivation
print("\n--- Refreshing mv_dormant_reactivation ---")
conn = get_conn()
conn.autocommit = True
t0 = time.time()
try:
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW mv_dormant_reactivation;")
    print(f"OK in {time.time()-t0:.1f}s")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    conn.close()

time.sleep(3)

# Refresh mv_branch_resurrection_2024_2026
print("--- Refreshing mv_branch_resurrection_2024_2026 ---")
conn = get_conn()
conn.autocommit = True
t0 = time.time()
try:
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW mv_branch_resurrection_2024_2026;")
    print(f"OK in {time.time()-t0:.1f}s")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    conn.close()

print("\nDone! Refresh the Campaign Analysis page.")
