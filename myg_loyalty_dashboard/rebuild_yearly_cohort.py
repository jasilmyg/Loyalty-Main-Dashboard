"""
Fast rebuild of mv_yearly_cohort.
Approach: NO revenue CTE (too slow). Build counts only from mv_customer_active_years.
Result set is tiny (< 100 rows) so it builds in seconds.
Revenue can be added later if needed.
"""
import os, django, time, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from django.core.cache import cache

def run(sql, label=""):
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    elapsed = time.time() - t0
    if label:
        print(f"  OK ({elapsed:.1f}s): {label}")
    return elapsed

def q1(sql):
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()

def q(sql):
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()

# ── 1. Cancel any lingering lock on mv_yearly_cohort ─────────────────────────
print("Cancelling any running queries on mv_yearly_cohort...")
try:
    with connection.cursor() as cur:
        cur.execute("""
            SELECT pg_cancel_backend(pid)
            FROM pg_stat_activity
            WHERE query ILIKE '%mv_yearly_cohort%'
              AND state != 'idle'
              AND pid != pg_backend_pid()
        """)
        killed = cur.fetchall()
        if killed:
            print(f"  Cancelled {len(killed)} query(ies)")
        else:
            print("  No active queries to cancel")
except Exception as e:
    print(f"  (cancel error: {e})")

import time; time.sleep(2)

# ── 2. Verify mv_customer_active_years ───────────────────────────────────────
row = q1("SELECT COUNT(*) FROM mv_customer_active_years")
print(f"\nmv_customer_active_years: {row[0]:,} rows  OK")

# ── 3. Build mv_yearly_cohort (counts only, no revenue join) ─────────────────
print("\nBuilding mv_yearly_cohort (fast — counts only)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort")

run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH
-- Cohort year: earliest year each customer was active (handles fv/lv swap)
cohort AS (
    SELECT
        a.mobile,
        MIN(a.active_year)::INT AS cohort_year
    FROM mv_customer_active_years a
    GROUP BY a.mobile
),
sizes AS (
    SELECT cohort_year::TEXT, COUNT(DISTINCT mobile)::BIGINT AS initial_size
    FROM cohort
    GROUP BY cohort_year
),
yearly AS (
    SELECT
        c.cohort_year::TEXT                    AS cohort_year,
        (a.active_year - c.cohort_year)        AS year_index,
        COUNT(DISTINCT a.mobile)::BIGINT       AS active_customers
    FROM mv_customer_active_years a
    JOIN cohort c ON a.mobile = c.mobile
    WHERE (a.active_year - c.cohort_year) BETWEEN 0 AND 10
    GROUP BY c.cohort_year, (a.active_year - c.cohort_year)
),
nrp AS (
    -- Customers whose ONLY active year = their cohort year (never returned)
    SELECT c.cohort_year::TEXT, COUNT(DISTINCT c.mobile)::BIGINT AS no_return_purchases
    FROM cohort c
    JOIN (
        SELECT mobile, COUNT(DISTINCT active_year) AS yr_count
        FROM mv_customer_active_years
        GROUP BY mobile
    ) cnt ON cnt.mobile = c.mobile
    WHERE cnt.yr_count = 1
    GROUP BY c.cohort_year
)
SELECT
    y.cohort_year,
    y.year_index,
    y.active_customers,
    0::FLOAT                                                           AS year_revenue,
    s.initial_size,
    ROUND((y.active_customers * 100.0 / NULLIF(s.initial_size,0))::NUMERIC, 2) AS retention_rate,
    0::BIGINT                                                          AS one_time_buyers,
    COALESCE(n.no_return_purchases, 0)::BIGINT                         AS no_return_purchases
FROM yearly y
JOIN sizes  s ON s.cohort_year = y.cohort_year
LEFT JOIN nrp n ON n.cohort_year = y.cohort_year
ORDER BY y.cohort_year DESC, y.year_index ASC
WITH DATA
""", "mv_yearly_cohort created")

run("CREATE UNIQUE INDEX idx_mv_yearly_cohort ON mv_yearly_cohort(cohort_year, year_index)", "unique index")

# ── 4. Verify ────────────────────────────────────────────────────────────────
print("\nVerification:")
rows = q("SELECT cohort_year, year_index, active_customers, initial_size, retention_rate, no_return_purchases FROM mv_yearly_cohort ORDER BY cohort_year, year_index")
print(f"  Rows: {len(rows)}  |  Negative yr_index: {sum(1 for r in rows if r[1]<0)}")
for r in rows:
    nrp_pct = float(r[5])/float(r[3])*100 if r[3] else 0
    nrp_str = f"  NRP={int(r[5]):,} ({nrp_pct:.1f}%)" if r[1]==0 else ""
    print(f"  {r[0]} yr{r[1]}: {int(r[2]):,}/{int(r[3]):,} = {float(r[4]):.1f}%{nrp_str}")

# ── 5. Benchmark ─────────────────────────────────────────────────────────────
t0 = time.time()
for _ in range(10):
    q("SELECT * FROM mv_yearly_cohort ORDER BY cohort_year DESC, year_index")
print(f"\n  Query avg: {(time.time()-t0)/10*1000:.1f}ms")

# ── 6. Clear cache ────────────────────────────────────────────────────────────
cache.delete('yearly_cohort_global')
cache.delete('cohort_retention_global')
print("  Cache cleared — next request loads from MV.")
print("\nDone!")
