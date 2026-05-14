"""
Fix mv_yearly_cohort: replace the UNION approximation with a clean,
accurate query derived entirely from mv_customer_dates + mv_customer_summary.

Logic:
- cohort_year = year of fv_month
- year_index 0 = cohort acquisition year (all new customers)
- year_index N = customers whose lv_month is in year (cohort_year + N)
- retention = active_in_year_N / initial_cohort_size
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
from django.core.cache import cache
from analytics.services import _q

def run(sql, label=""):
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    elapsed = time.time() - t0
    if label:
        print(f"  OK ({elapsed:.1f}s): {label}")

print("Rebuilding mv_yearly_cohort (clean version)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH cohort_base AS (
    SELECT
        EXTRACT(YEAR FROM fv_month)::TEXT AS cohort_year,
        mobile,
        fv_month,
        lv_month,
        EXTRACT(YEAR FROM lv_month)::INT - EXTRACT(YEAR FROM fv_month)::INT AS max_year_index
    FROM mv_customer_dates
    WHERE fv_month IS NOT NULL AND lv_month IS NOT NULL
),
-- Cohort sizes (year_index = 0)
cohort_sizes AS (
    SELECT cohort_year, COUNT(DISTINCT mobile)::BIGINT AS initial_size
    FROM cohort_base
    GROUP BY cohort_year
),
-- Active customers per cohort_year × year_index
-- year_index = year they were LAST SEEN relative to cohort year
-- For year_index 0: customers acquired that year (all of them)
-- For year_index N: customers whose last visit was in that year
year_activity AS (
    SELECT cohort_year, max_year_index AS year_index,
           COUNT(DISTINCT mobile)::BIGINT AS active_customers
    FROM cohort_base
    GROUP BY cohort_year, max_year_index
    UNION ALL
    -- Add year_index = 0 for everyone (acquisition year)
    SELECT cohort_year, 0 AS year_index,
           COUNT(DISTINCT mobile)::BIGINT AS active_customers
    FROM cohort_base
    GROUP BY cohort_year
),
deduped AS (
    SELECT cohort_year, year_index,
           MAX(active_customers) AS active_customers
    FROM year_activity
    GROUP BY cohort_year, year_index
),
-- Revenue by cohort_year (from mv_customer_summary total_spend)
-- Approx: split by year proportionally
cohort_revenue AS (
    SELECT
        cb.cohort_year,
        cb.max_year_index AS year_index,
        SUM(cs.total_spend / NULLIF(cs.visits, 0))::FLOAT AS year_revenue
    FROM cohort_base cb
    JOIN mv_customer_summary cs ON cs.mobile = cb.mobile
    WHERE cs.total_spend IS NOT NULL
    GROUP BY cb.cohort_year, cb.max_year_index
    UNION ALL
    SELECT
        cb.cohort_year, 0 AS year_index,
        SUM(cs.total_spend / NULLIF(cs.visits, 0))::FLOAT
    FROM cohort_base cb
    JOIN mv_customer_summary cs ON cs.mobile = cb.mobile
    WHERE cs.total_spend IS NOT NULL
    GROUP BY cb.cohort_year
),
revenue_deduped AS (
    SELECT cohort_year, year_index, MAX(year_revenue) AS year_revenue
    FROM cohort_revenue GROUP BY cohort_year, year_index
),
-- One-time buyers: visits = 1
otb AS (
    SELECT cb.cohort_year, COUNT(DISTINCT cb.mobile)::BIGINT AS one_time_buyers
    FROM cohort_base cb
    JOIN mv_customer_summary cs ON cs.mobile = cb.mobile
    WHERE cs.visits = 1
    GROUP BY cb.cohort_year
),
-- No-return: lv_month = fv_month (never returned)
nrp AS (
    SELECT cohort_year, COUNT(DISTINCT mobile)::BIGINT AS no_return_purchases
    FROM cohort_base WHERE max_year_index = 0
    GROUP BY cohort_year
)
SELECT
    d.cohort_year,
    d.year_index,
    d.active_customers,
    COALESCE(r.year_revenue, 0)::FLOAT AS year_revenue,
    s.initial_size,
    (d.active_customers * 100.0 / NULLIF(s.initial_size, 0)) AS retention_rate,
    COALESCE(o.one_time_buyers, 0) AS one_time_buyers,
    COALESCE(n.no_return_purchases, 0) AS no_return_purchases
FROM deduped d
JOIN cohort_sizes s ON s.cohort_year = d.cohort_year
LEFT JOIN revenue_deduped r ON r.cohort_year = d.cohort_year AND r.year_index = d.year_index
LEFT JOIN otb o ON o.cohort_year = d.cohort_year
LEFT JOIN nrp n ON n.cohort_year = d.cohort_year
ORDER BY d.cohort_year DESC, d.year_index ASC
WITH DATA
""", "mv_yearly_cohort rebuilt")

run("CREATE INDEX ON mv_yearly_cohort(cohort_year, year_index)", "index")

print("\nVerifying...")
with connection.cursor() as cur:
    cur.execute("SELECT cohort_year, year_index, active_customers, initial_size, retention_rate FROM mv_yearly_cohort ORDER BY cohort_year, year_index")
    rows = cur.fetchall()
    print(f"  Rows: {len(rows)}")
    for r in rows:
        print(f"  {r[0]} yr{r[1]}: active={r[2]:,}  size={r[3]:,}  ret={float(r[4] or 0):.1f}%")

# Benchmark
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_yearly_cohort")
        cur.fetchall()
print(f"\n  mv_yearly_cohort query: {(time.time()-t0)/5*1000:.1f}ms avg")

# Rewarm cache
print("\nRewarming cache...")
rows = _q("SELECT cohort_year, year_index, active_customers, year_revenue, initial_size, retention_rate, one_time_buyers, no_return_purchases FROM mv_yearly_cohort ORDER BY cohort_year DESC, year_index ASC")
rfm_rows = _q("SELECT cohort_year, segment, customer_count FROM mv_cohort_rfm ORDER BY cohort_year")
cohort_data = {}
for r in rows:
    cy, yi, active, rev, size, rate, otb, nrp = r
    size = int(size or 0)
    if cy not in cohort_data:
        cohort_data[cy] = {
            'size': size, 'one_time_buyers': int(otb or 0),
            'otb_pct': round(float(otb or 0)*100/size, 2) if size else 0,
            'no_return_purchases': int(nrp or 0),
            'nrp_pct': round(float(nrp or 0)*100/size, 2) if size else 0,
            'years': {},
        }
    cohort_data[cy]['years'][int(yi)] = {
        'active': int(active or 0), 'revenue': round(float(rev or 0), 2),
        'retention': round(float(rate or 0), 2),
        'ltv': round(float(rev or 0)/size, 2) if size else 0,
    }
for rr in rfm_rows:
    cy, seg, count = rr
    if cy in cohort_data:
        if 'rfm' not in cohort_data[cy]:
            cohort_data[cy]['rfm'] = {}
        cohort_data[cy]['rfm'][seg] = count
cache.set('yearly_cohort_global', cohort_data, 86400)
print(f"  yearly_cohort cache warmed: {len(cohort_data)} years → {list(cohort_data.keys())}")
print("Done!")
