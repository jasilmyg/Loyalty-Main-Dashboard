"""
Fix cohort retention calculations.

ROOT CAUSE:
  The current mv_yearly_cohort uses EXTRACT(YEAR FROM lv_month) - EXTRACT(YEAR FROM fv_month)
  as year_index. This means a customer active in 2021,2022,2023 only shows up in yr2
  (their LAST year), not yr1 or yr2 separately. Year 1 and Year 2 retention are massively
  undercounted.

FIX:
  Step 1: Create mv_customer_active_years (mobile, active_year) from raw v_sales_data.
          This gives us every year each customer transacted — one row per customer per year.

  Step 2: Rebuild mv_yearly_cohort by joining cohort_year (from mv_customer_dates) 
          with mv_customer_active_years to count DISTINCT customers active in each year.

  This is the CORRECT cohort retention definition:
    Year N retention = (customers in cohort who transacted in cohort_year+N) / cohort_size
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

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: mv_customer_active_years — (mobile, active_year)
# ─────────────────────────────────────────────────────────────────────────────
print("Step 1: Creating mv_customer_active_years...")
print("  (scanning v_sales_data for all customer-year pairs — takes 2-5 min)")
run("DROP MATERIALIZED VIEW IF EXISTS mv_customer_active_years CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_customer_active_years AS
SELECT
    "Customer Mobile"            AS mobile,
    EXTRACT(YEAR FROM "Date")::INT AS active_year
FROM v_sales_data
WHERE "Customer Mobile" ~ '^[0-9]{10}$'
  AND "Date" IS NOT NULL
GROUP BY "Customer Mobile", EXTRACT(YEAR FROM "Date")::INT
WITH DATA
""", "mv_customer_active_years created")

run("CREATE INDEX ON mv_customer_active_years(mobile)", "index on mobile")
run("CREATE INDEX ON mv_customer_active_years(active_year)", "index on active_year")
run("CREATE INDEX ON mv_customer_active_years(mobile, active_year)", "composite index")

row = _q1("SELECT COUNT(*) FROM mv_customer_active_years")
print(f"  Rows: {row[0]:,}")

# Peek at data
rows = _q("SELECT active_year, COUNT(DISTINCT mobile) FROM mv_customer_active_years GROUP BY active_year ORDER BY active_year")
print("  Year distribution:")
for r in rows:
    print(f"    {r[0]}: {r[1]:,} customers")

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Rebuild mv_yearly_cohort using correct year-by-year activity
# ─────────────────────────────────────────────────────────────────────────────
print("\nStep 2: Rebuilding mv_yearly_cohort (correct version)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH
-- Cohort year = year of first visit (using LEAST to fix any fv/lv swaps)
cohort_membership AS (
    SELECT
        mobile,
        EXTRACT(YEAR FROM LEAST(fv_month, lv_month))::INT AS cohort_year
    FROM mv_customer_dates
    WHERE fv_month IS NOT NULL AND lv_month IS NOT NULL
),

-- Base cohort sizes
cohort_sizes AS (
    SELECT cohort_year::TEXT, COUNT(DISTINCT mobile)::BIGINT AS initial_size
    FROM cohort_membership
    GROUP BY cohort_year
),

-- For each cohort: count how many customers were active in each subsequent year
-- year_index = active_year - cohort_year (0 = acquisition year, 1 = next year, etc.)
year_activity AS (
    SELECT
        c.cohort_year::TEXT    AS cohort_year,
        (a.active_year - c.cohort_year) AS year_index,
        COUNT(DISTINCT a.mobile)::BIGINT AS active_customers
    FROM mv_customer_active_years a
    JOIN cohort_membership c ON a.mobile = c.mobile
    WHERE (a.active_year - c.cohort_year) >= 0   -- no negatives
      AND (a.active_year - c.cohort_year) <= 10  -- cap at 10 years
    GROUP BY c.cohort_year, year_index
),

-- Revenue per cohort year (approximate using total_spend from mv_customer_summary)
cohort_revenue AS (
    SELECT
        c.cohort_year::TEXT AS cohort_year,
        (a.active_year - c.cohort_year) AS year_index,
        SUM(cs.total_spend / NULLIF(cs.visits, 0))::FLOAT AS year_revenue
    FROM mv_customer_active_years a
    JOIN cohort_membership c ON a.mobile = c.mobile
    JOIN mv_customer_summary cs ON cs.mobile = a.mobile
    WHERE (a.active_year - c.cohort_year) >= 0
      AND (a.active_year - c.cohort_year) <= 10
      AND cs.total_spend IS NOT NULL
    GROUP BY c.cohort_year, year_index
),

-- One-time buyers: customers with only 1 distinct year of activity
otb AS (
    SELECT c.cohort_year::TEXT AS cohort_year,
           COUNT(DISTINCT c.mobile)::BIGINT AS one_time_buyers
    FROM cohort_membership c
    JOIN (
        SELECT mobile, COUNT(DISTINCT active_year) AS active_years
        FROM mv_customer_active_years
        GROUP BY mobile
    ) ay ON ay.mobile = c.mobile
    WHERE ay.active_years = 1
    GROUP BY c.cohort_year
),

-- No Return Purchases: customers whose only active year was their cohort year
nrp AS (
    SELECT c.cohort_year::TEXT AS cohort_year,
           COUNT(DISTINCT c.mobile)::BIGINT AS no_return_purchases
    FROM cohort_membership c
    JOIN (
        SELECT mobile, MAX(active_year) AS max_year, MIN(active_year) AS min_year
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
JOIN cohort_sizes s ON s.cohort_year = y.cohort_year
LEFT JOIN cohort_revenue r
       ON r.cohort_year = y.cohort_year AND r.year_index = y.year_index
LEFT JOIN otb o ON o.cohort_year = y.cohort_year
LEFT JOIN nrp n ON n.cohort_year = y.cohort_year
ORDER BY y.cohort_year DESC, y.year_index ASC
WITH DATA
""", "mv_yearly_cohort rebuilt (correct)")

run("CREATE UNIQUE INDEX ON mv_yearly_cohort(cohort_year, year_index)", "unique index")

print("\nVerifying results:")
with connection.cursor() as cur:
    cur.execute("SELECT cohort_year, year_index, active_customers, initial_size, retention_rate FROM mv_yearly_cohort ORDER BY cohort_year, year_index")
    rows = cur.fetchall()
    print(f"  Total rows: {len(rows)}")
    neg = [r for r in rows if r[1] < 0]
    print(f"  Negative year_index: {len(neg)}  (should be 0)")
    for r in rows:
        print(f"  {r[0]} yr{r[1]}: {r[2]:,} / {r[3]:,} = {float(r[4] or 0):.1f}%")

# Benchmark
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_yearly_cohort ORDER BY cohort_year DESC, year_index ASC")
        cur.fetchall()
print(f"\n  Query avg: {(time.time()-t0)/5*1000:.1f}ms")

# Clear old cache so next request hits fresh MV
from django.core.cache import cache
cache.delete('yearly_cohort_global')
cache.delete('cohort_retention_global')
print("\nCache cleared. Next request will load correct data from MV.")
print("Done!")
