"""
Fix mv_yearly_cohort duplicate rows.

ROOT CAUSE:
  mv_customer_dates has multiple rows per mobile (duplicate cohort_year per mobile).
  This causes cohort_membership to fan out, producing a cross-join with year_activity
  and cohort_sizes, resulting in 4 rows per (cohort_year, year_index).

FIX:
  Rebuild mv_yearly_cohort using:
  - cohort_membership with DISTINCT (one row per mobile, one cohort_year)
  - cohort_year = MIN(first_year) from mv_customer_active_years directly
    (avoids dependency on potentially duplicate mv_customer_dates)
  - Revenue from the actual sales table, properly summed per (cohort_year, active_year)
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from analytics.services import _q, _q1

def run(sql, label=""):
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    elapsed = time.time() - t0
    if label:
        print(f"  OK ({elapsed:.1f}s): {label}")
    return elapsed

# ── 0. Diagnose duplicate source ─────────────────────────────────────────────
print("=== Diagnosing source of duplicates ===")
r = _q1("SELECT COUNT(*) FROM mv_customer_dates")
print(f"  mv_customer_dates rows: {r[0]:,}")
r2 = _q1("SELECT COUNT(DISTINCT mobile) FROM mv_customer_dates")
print(f"  mv_customer_dates distinct mobiles: {r2[0]:,}")
if r[0] != r2[0]:
    print(f"  ⚠ DUPLICATE mobile rows: {r[0]-r2[0]:,} extras — this is the bug!")
else:
    print("  mv_customer_dates looks clean")

# Also check mv_customer_active_years
r3 = _q1("SELECT COUNT(*) FROM mv_customer_active_years")
r4 = _q1("SELECT COUNT(*) FROM (SELECT DISTINCT mobile, active_year FROM mv_customer_active_years) t")
print(f"  mv_customer_active_years rows: {r3[0]:,}, distinct (mobile,year): {r4[0]:,}")

# ── 1. Cancel any lingering locks ────────────────────────────────────────────
print("\nCancelling locks on mv_yearly_cohort...")
with connection.cursor() as cur:
    cur.execute("""
        SELECT pg_cancel_backend(pid)
        FROM pg_stat_activity
        WHERE query ILIKE '%mv_yearly_cohort%'
          AND pid <> pg_backend_pid()
    """)

# ── 2. Rebuild mv_yearly_cohort with clean, deduped logic ────────────────────
print("\nRebuilding mv_yearly_cohort (deduplicated)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")

run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH
-- Step A: Get cohort_year = the EARLIEST year each customer was active.
-- Use mv_customer_active_years directly (no dependency on potentially-duplicate mv_customer_dates).
cohort_membership AS (
    SELECT
        mobile,
        MIN(active_year) AS cohort_year   -- one row per mobile, always
    FROM mv_customer_active_years
    GROUP BY mobile
),

-- Step B: Cohort sizes (unique customers per cohort year)
cohort_sizes AS (
    SELECT
        cohort_year::TEXT                      AS cohort_year,
        COUNT(DISTINCT mobile)::BIGINT         AS initial_size
    FROM cohort_membership
    GROUP BY cohort_year
),

-- Step C: For each cohort, count customers active in each subsequent year
year_activity AS (
    SELECT
        c.cohort_year::TEXT                    AS cohort_year,
        (a.active_year - c.cohort_year)        AS year_index,
        COUNT(DISTINCT a.mobile)::BIGINT       AS active_customers
    FROM mv_customer_active_years a
    JOIN cohort_membership c ON a.mobile = c.mobile
    WHERE (a.active_year - c.cohort_year) >= 0
      AND (a.active_year - c.cohort_year) <= 10
    GROUP BY c.cohort_year, (a.active_year - c.cohort_year)
),

-- Step D: Revenue — approximate as total_spend / visits * (1/active_years)
-- One row per (cohort_year, year_index)
cohort_revenue AS (
    SELECT
        c.cohort_year::TEXT                            AS cohort_year,
        (a.active_year - c.cohort_year)               AS year_index,
        SUM(
            cs.total_spend / NULLIF(cs.visits, 0)
        )::FLOAT                                       AS year_revenue
    FROM (SELECT DISTINCT mobile, active_year FROM mv_customer_active_years) a
    JOIN cohort_membership c ON a.mobile = c.mobile
    JOIN mv_customer_summary cs ON cs.mobile = a.mobile
    WHERE (a.active_year - c.cohort_year) >= 0
      AND (a.active_year - c.cohort_year) <= 10
      AND cs.total_spend IS NOT NULL
    GROUP BY c.cohort_year, (a.active_year - c.cohort_year)
),

-- Step E: One-time buyers (only 1 distinct year ever)
otb AS (
    SELECT
        c.cohort_year::TEXT                            AS cohort_year,
        COUNT(DISTINCT c.mobile)::BIGINT               AS one_time_buyers
    FROM cohort_membership c
    JOIN (
        SELECT mobile, COUNT(DISTINCT active_year) AS yrs
        FROM mv_customer_active_years
        GROUP BY mobile
    ) ay ON ay.mobile = c.mobile
    WHERE ay.yrs = 1
    GROUP BY c.cohort_year
),

-- Step F: No-return purchases (never came back after cohort year)
nrp AS (
    SELECT
        c.cohort_year::TEXT                            AS cohort_year,
        COUNT(DISTINCT c.mobile)::BIGINT               AS no_return_purchases
    FROM cohort_membership c
    JOIN (
        SELECT mobile, MAX(active_year) AS max_year
        FROM mv_customer_active_years
        GROUP BY mobile
    ) ay ON ay.mobile = c.mobile
    WHERE ay.max_year = c.cohort_year
    GROUP BY c.cohort_year
)

SELECT
    y.cohort_year,
    y.year_index,
    y.active_customers,
    COALESCE(r.year_revenue, 0)::FLOAT                              AS year_revenue,
    s.initial_size,
    (y.active_customers * 100.0 / NULLIF(s.initial_size, 0))       AS retention_rate,
    COALESCE(o.one_time_buyers, 0)                                  AS one_time_buyers,
    COALESCE(n.no_return_purchases, 0)                              AS no_return_purchases
FROM year_activity y
JOIN cohort_sizes s   ON s.cohort_year = y.cohort_year
LEFT JOIN cohort_revenue r
       ON r.cohort_year = y.cohort_year AND r.year_index = y.year_index
LEFT JOIN otb o       ON o.cohort_year = y.cohort_year
LEFT JOIN nrp n       ON n.cohort_year = y.cohort_year
ORDER BY y.cohort_year DESC, y.year_index ASC
WITH DATA
""", "mv_yearly_cohort rebuilt")

run("CREATE UNIQUE INDEX ON mv_yearly_cohort(cohort_year, year_index)", "unique index")

# ── 3. Verify ─────────────────────────────────────────────────────────────────
print("\n=== Verification ===")

# Check for duplicates
dup_rows = _q("SELECT cohort_year, year_index, COUNT(*) as cnt FROM mv_yearly_cohort GROUP BY cohort_year, year_index HAVING COUNT(*) > 1")
if dup_rows:
    print(f"  ⚠ Still have duplicates:")
    for r in dup_rows:
        print(f"    {r[0]} yr{r[1]}: {r[2]} rows")
else:
    print("  ✓ No duplicate rows — clean!")

# Print retention table
rows = _q("SELECT cohort_year, year_index, active_customers, initial_size, retention_rate FROM mv_yearly_cohort ORDER BY cohort_year DESC, year_index ASC")
print(f"\n  Total rows: {len(rows)}")
print(f"  {'Cohort':<8} {'Yr':<4} {'Active':>10} {'Size':>10} {'Retention':>10}")
print("  " + "-"*46)
for r in rows:
    print(f"  {r[0]:<8} {r[1]:<4} {r[2]:>10,} {r[3]:>10,} {float(r[4] or 0):>9.2f}%")

# ── 4. Clear cache ────────────────────────────────────────────────────────────
from django.core.cache import cache
cache.delete('yearly_cohort_global')
cache.delete('cohort_retention_global')
print("\n✓ Cache cleared. Refresh the dashboard to see corrected values.")
