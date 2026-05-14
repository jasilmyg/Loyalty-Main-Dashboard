"""
Create mv_monetary_quintiles - pre-aggregated 5-row MV for instant quintile queries.
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

def run(sql, label=""):
    with connection.cursor() as cur:
        cur.execute(sql)
    if label:
        print(f"  OK: {label}")

print("Creating mv_monetary_quintiles...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_monetary_quintiles CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_monetary_quintiles AS
WITH quintiled AS (
    SELECT total_spend,
           NTILE(5) OVER (ORDER BY total_spend DESC) AS quintile
    FROM mv_customer_summary
    WHERE total_spend IS NOT NULL
)
SELECT
    quintile,
    AVG(total_spend)::FLOAT AS avg_spend,
    COUNT(*)::BIGINT        AS customer_count
FROM quintiled
GROUP BY quintile
ORDER BY quintile
WITH DATA
""", "mv_monetary_quintiles created")

run("CREATE UNIQUE INDEX ON mv_monetary_quintiles(quintile)", "index created")

print("\nVerifying...")
with connection.cursor() as cur:
    cur.execute("SELECT * FROM mv_monetary_quintiles ORDER BY quintile")
    rows = cur.fetchall()
    labels = {1:'Top 20%', 2:'Next 20%', 3:'Middle 20%', 4:'Next 20%', 5:'Bottom 20%'}
    for r in rows:
        print(f"  {labels[r[0]]}: avg={r[1]:,.0f}  count={r[2]:,}")

print("\nBenchmarking SELECT on mv_monetary_quintiles...")
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_monetary_quintiles ORDER BY quintile")
        cur.fetchall()
elapsed = (time.time() - t0) / 5
print(f"  Avg time per query: {elapsed*1000:.1f}ms  (was 7700ms)")

print("\nDone!")
