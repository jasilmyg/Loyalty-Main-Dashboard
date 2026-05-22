"""
Fix the LTV (year_revenue) calculation in mv_yearly_cohort.

ROOT CAUSE:
  Currently, year_revenue is calculated using an approximation: 
  SUM(cs.total_spend / cs.visits) for all active customers.
  This takes the customer's *lifetime average transaction value* and adds it 
  to every year they are active, rather than summing their *actual spend* in that year.
  This leads to inflated Year 1+ LTV and deflated Year 0 LTV.

FIX:
  1. Rebuild mv_customer_active_years to include `yearly_spend = SUM("Total Value")`.
  2. Rebuild mv_yearly_cohort to sum this precise `yearly_spend` per cohort year.
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

# ── 1. Rebuild mv_customer_active_years with yearly_spend ────────────────────
print("\nStep 1: Rebuilding mv_customer_active_years to include yearly_spend...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_customer_active_years CASCADE")

run("""
CREATE MATERIALIZED VIEW mv_customer_active_years AS
SELECT
    "Customer Mobile"            AS mobile,
    EXTRACT(YEAR FROM "Date")::INT AS active_year,
    SUM("Total Value")::FLOAT    AS yearly_spend
FROM v_sales_data
WHERE "Customer Mobile" ~ '^[0-9]{10}$'
  AND "Date" IS NOT NULL
GROUP BY "Customer Mobile", EXTRACT(YEAR FROM "Date")::INT
WITH DATA
""", "mv_customer_active_years rebuilt")

run("CREATE INDEX ON mv_customer_active_years(mobile)", "index on mobile")
run("CREATE INDEX ON mv_customer_active_years(active_year)", "index on active_year")
run("CREATE INDEX ON mv_customer_active_years(mobile, active_year)", "composite index")

# ── 2. Cancel any lingering locks ────────────────────────────────────────────
print("\nCancelling locks on mv_yearly_cohort...")
with connection.cursor() as cur:
    cur.execute("""
        SELECT pg_cancel_backend(pid)
        FROM pg_stat_activity
        WHERE query ILIKE '%mv_yearly_cohort%'
          AND pid <> pg_backend_pid()
    """)

# ── 3. Rebuild mv_yearly_cohort with accurate year_revenue ───────────────────
print("\nStep 2: Rebuilding mv_yearly_cohort with accurate LTV revenue...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")

run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH
-- Step A: Get cohort_year = the EARLIEST year each customer was active.
cohort_membership AS (
    SELECT
        mobile,
        MIN(active_year) AS cohort_year
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

-- Step D: Revenue — sum of actual yearly_spend for active customers!
cohort_revenue AS (
    SELECT
        c.cohort_year::TEXT                            AS cohort_year,
        (a.active_year - c.cohort_year)               AS year_index,
        SUM(a.yearly_spend)::FLOAT                     AS year_revenue
    FROM mv_customer_active_years a
    JOIN cohort_membership c ON a.mobile = c.mobile
    WHERE (a.active_year - c.cohort_year) >= 0
      AND (a.active_year - c.cohort_year) <= 10
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

# ── 4. Verify ─────────────────────────────────────────────────────────────────
print("\n=== Verification ===")

# Print new LTV table
rows = _q("SELECT cohort_year, year_index, active_customers, initial_size, year_revenue FROM mv_yearly_cohort ORDER BY cohort_year DESC, year_index ASC LIMIT 10")
for r in rows:
    print(f"  {r[0]} yr{r[1]}: rev={r[4]:,.2f} / size={r[3]} => ltv={r[4]/r[3]:,.2f}")

# Clear cache
from django.core.cache import cache
cache.delete('yearly_cohort_global')
cache.delete('cohort_retention_global')
print("\nCache cleared. Run 'python manage.py runserver' again if needed.")
