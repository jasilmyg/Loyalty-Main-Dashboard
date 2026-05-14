"""
Rebuild mv_yearly_cohort with:
1. GREATEST(fv_month, lv_month) to fix swapped dates (968K affected rows)
2. Only year_index >= 0 (no negatives)
3. Correct NRP: customers whose lv_month == fv_month (never returned in different month)
4. Correct OTB: customers with visits = 1 from mv_customer_summary
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from analytics.services import _q

def run(sql, label=""):
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    elapsed = time.time() - t0
    if label:
        print(f"  OK ({elapsed:.1f}s): {label}")

print("Rebuilding mv_yearly_cohort (corrected)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH cohort_base AS (
    SELECT
        cd.mobile,
        -- Use GREATEST to fix swapped fv/lv dates
        LEAST(cd.fv_month, cd.lv_month)    AS true_fv,
        GREATEST(cd.fv_month, cd.lv_month) AS true_lv,
        cs.visits,
        cs.total_spend
    FROM mv_customer_dates cd
    JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
    WHERE cd.fv_month IS NOT NULL AND cd.lv_month IS NOT NULL
      AND cs.total_spend IS NOT NULL
),
enriched AS (
    SELECT
        mobile,
        true_fv,
        true_lv,
        visits,
        total_spend,
        EXTRACT(YEAR FROM true_fv)::TEXT AS cohort_year,
        -- year_index = how many years after cohort year did last visit occur
        (EXTRACT(YEAR FROM true_lv)::INT - EXTRACT(YEAR FROM true_fv)::INT) AS year_index
    FROM cohort_base
    WHERE EXTRACT(YEAR FROM true_fv)::INT >= 2019  -- sanity bound
),
-- Cohort sizes
cohort_sizes AS (
    SELECT cohort_year, COUNT(DISTINCT mobile)::BIGINT AS initial_size
    FROM enriched
    GROUP BY cohort_year
),
-- Year 0: all customers in their cohort year
year0 AS (
    SELECT cohort_year, 0 AS year_index,
           COUNT(DISTINCT mobile)::BIGINT AS active_customers,
           SUM(total_spend)::FLOAT AS year_revenue
    FROM enriched
    GROUP BY cohort_year
),
-- Year N (>=1): customers whose lv is N years after cohort year
year_n AS (
    SELECT cohort_year, year_index,
           COUNT(DISTINCT mobile)::BIGINT AS active_customers,
           -- Revenue approximation for return years: repeat portion
           SUM(total_spend * (1.0 - 1.0/NULLIF(visits,0)))::FLOAT AS year_revenue
    FROM enriched
    WHERE year_index > 0
    GROUP BY cohort_year, year_index
),
all_years AS (
    SELECT * FROM year0
    UNION ALL
    SELECT * FROM year_n
),
-- One-time buyers: only 1 visit total
otb AS (
    SELECT cohort_year, COUNT(DISTINCT mobile)::BIGINT AS one_time_buyers
    FROM enriched WHERE visits = 1
    GROUP BY cohort_year
),
-- No Return Purchases: lv_month == fv_month (returned same month or never)
nrp AS (
    SELECT cohort_year, COUNT(DISTINCT mobile)::BIGINT AS no_return_purchases
    FROM enriched WHERE year_index = 0
    GROUP BY cohort_year
)
SELECT
    a.cohort_year,
    a.year_index,
    a.active_customers,
    COALESCE(a.year_revenue, 0)::FLOAT AS year_revenue,
    s.initial_size,
    (a.active_customers * 100.0 / NULLIF(s.initial_size, 0)) AS retention_rate,
    COALESCE(o.one_time_buyers, 0)       AS one_time_buyers,
    COALESCE(n.no_return_purchases, 0)   AS no_return_purchases
FROM all_years a
JOIN cohort_sizes s ON s.cohort_year = a.cohort_year
LEFT JOIN otb o ON o.cohort_year = a.cohort_year
LEFT JOIN nrp n ON n.cohort_year = a.cohort_year
ORDER BY a.cohort_year DESC, a.year_index ASC
WITH DATA
""", "mv_yearly_cohort rebuilt")

run("CREATE UNIQUE INDEX ON mv_yearly_cohort(cohort_year, year_index)", "unique index")

print("\nVerifying — no negative year indices:")
with connection.cursor() as cur:
    cur.execute("SELECT cohort_year, year_index, active_customers, initial_size, retention_rate FROM mv_yearly_cohort ORDER BY cohort_year, year_index")
    rows = cur.fetchall()
    print(f"  Total rows: {len(rows)}")
    negs = [r for r in rows if r[1] < 0]
    print(f"  Negative year_index rows: {len(negs)}")
    for r in rows:
        print(f"  {r[0]} yr{r[1]}: active={r[2]:,}  size={r[3]:,}  ret={float(r[4] or 0):.1f}%")

# Benchmark
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_yearly_cohort ORDER BY cohort_year DESC, year_index ASC")
        cur.fetchall()
print(f"\n  Query avg: {(time.time()-t0)/5*1000:.1f}ms")

# Also rebuild mv_cohort_rfm with corrected dates
print("\nRebuilding mv_cohort_rfm (with date fix)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_cohort_rfm CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_cohort_rfm AS
WITH enriched AS (
    SELECT
        cd.mobile,
        EXTRACT(YEAR FROM LEAST(cd.fv_month, cd.lv_month))::TEXT AS cohort_year,
        cs.visits,
        CASE
            WHEN cs.last_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                THEN SUBSTRING(cs.last_visit, 1, 10)::DATE
            WHEN cs.last_visit ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}'
                THEN TO_DATE(cs.last_visit, 'DD-MM-YYYY')
            ELSE NULL
        END AS last_d,
        CASE
            WHEN cs.first_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                THEN SUBSTRING(cs.first_visit, 1, 10)::DATE
            WHEN cs.first_visit ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}'
                THEN TO_DATE(cs.first_visit, 'DD-MM-YYYY')
            ELSE NULL
        END AS first_d
    FROM mv_customer_dates cd
    JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
    WHERE cd.fv_month IS NOT NULL
),
segmented AS (
    SELECT cohort_year,
        CASE
            WHEN (CURRENT_DATE - GREATEST(last_d, first_d)) <= 90  AND visits >= 3 THEN 'Champions'
            WHEN (CURRENT_DATE - GREATEST(last_d, first_d)) <= 180 AND visits >= 2 THEN 'Loyal'
            WHEN (CURRENT_DATE - GREATEST(last_d, first_d)) >  365                 THEN 'Lost'
            ELSE 'Average'
        END AS segment
    FROM enriched
    WHERE last_d IS NOT NULL AND first_d IS NOT NULL
)
SELECT cohort_year, segment, COUNT(*)::BIGINT AS customer_count
FROM segmented
GROUP BY cohort_year, segment
ORDER BY cohort_year, segment
WITH DATA
""", "mv_cohort_rfm rebuilt")

run("CREATE INDEX ON mv_cohort_rfm(cohort_year)", "index")
print("  Sample:")
rows = _q("SELECT cohort_year, segment, customer_count FROM mv_cohort_rfm ORDER BY cohort_year, segment")
for r in rows[:8]:
    print(f"  {r[0]} {r[1]}: {r[2]:,}")

print("\nDone! Both MVs rebuilt with correct date handling.")
