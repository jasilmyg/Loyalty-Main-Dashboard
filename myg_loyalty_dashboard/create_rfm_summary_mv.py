"""
Create mv_rfm_summary - a pre-aggregated 6-row MV from mv_rfm_segments.
This replaces the GROUP BY on 4.2M rows with a direct SELECT on 6 rows.
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

print("Step 1: Creating mv_rfm_summary (pre-aggregated 6 rows)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_rfm_summary CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_rfm_summary AS
SELECT
    segment,
    COUNT(mobile)::BIGINT       AS customer_count,
    SUM(monetary)::FLOAT        AS total_revenue,
    AVG(monetary)::FLOAT        AS avg_revenue
FROM mv_rfm_segments
GROUP BY segment
ORDER BY customer_count DESC
WITH DATA
""", "mv_rfm_summary created")

run("CREATE UNIQUE INDEX ON mv_rfm_summary(segment)", "index on segment")

print("\nStep 2: Verifying...")
with connection.cursor() as cur:
    cur.execute("SELECT * FROM mv_rfm_summary ORDER BY customer_count DESC")
    rows = cur.fetchall()
    print(f"  Rows: {len(rows)}")
    for r in rows:
        print(f"    {r[0]}: {r[1]:,} customers  avg_rev={r[3]:,.0f}")

print("\nStep 3: Benchmark SELECT on mv_rfm_summary...")
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_rfm_summary ORDER BY customer_count DESC")
        cur.fetchall()
elapsed = (time.time() - t0) / 5
print(f"  Avg time per query: {elapsed*1000:.1f}ms")

print("\nDone!")
