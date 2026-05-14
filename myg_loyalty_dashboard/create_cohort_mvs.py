"""
Build three cohort MVs from pre-aggregated sources (no raw v_sales_data scan):

1. mv_yearly_cohort   — yearly cohort stats (active, revenue, retention %)
2. mv_cohort_rfm      — RFM health per cohort year
3. mv_cohort_retention — monthly cohort retention matrix (the heatmap data)

Strategy: build from mv_customer_summary + mv_customer_dates + mv_fy_members
to avoid scanning the 12M-row raw table.
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

def run(sql, label=""):
    t0 = time.time()
    with connection.cursor() as cur:
        cur.execute(sql)
    elapsed = time.time() - t0
    if label:
        print(f"  OK ({elapsed:.1f}s): {label}")
    return elapsed

# ─────────────────────────────────────────────────────────────────────────────
# MV 1: mv_yearly_cohort
# Uses mv_customer_dates (fv_month/lv_month) + mv_customer_summary (visits, total_spend)
# to compute per-cohort-year: active customers, revenue, retention, OTB, NRP.
# ─────────────────────────────────────────────────────────────────────────────
print("Creating mv_yearly_cohort...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH customer_cohorts AS (
    -- Use mv_customer_dates for first visit year
    SELECT
        cd.mobile,
        EXTRACT(YEAR FROM cd.fv_month)::TEXT AS cohort_year,
        cd.fv_month,
        cd.lv_month,
        cs.visits,
        cs.total_spend
    FROM mv_customer_dates cd
    JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
    WHERE cd.fv_month IS NOT NULL AND cs.total_spend IS NOT NULL
),
-- Revenue by cohort_year × year_index using monthly summary
monthly_rev AS (
    SELECT
        EXTRACT(YEAR FROM cd.fv_month)::TEXT AS cohort_year,
        (EXTRACT(YEAR FROM ms.month_date) - EXTRACT(YEAR FROM cd.fv_month))::INT AS year_index,
        COUNT(DISTINCT cd.mobile) AS active_customers,
        SUM(ms.revenue / NULLIF(ms.customers, 0) * 1)::FLOAT AS year_revenue_approx
    FROM mv_customer_dates cd
    JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
    CROSS JOIN LATERAL (
        SELECT month_date, revenue, customers
        FROM mv_monthly_summary
        WHERE month_date BETWEEN cd.fv_month AND cd.lv_month
          AND EXTRACT(YEAR FROM month_date) >= EXTRACT(YEAR FROM cd.fv_month)
        LIMIT 1
    ) ms
    WHERE cd.fv_month IS NOT NULL
    GROUP BY 1, 2
),
-- Simpler approach: just use customer_cohorts for yearly stats
yearly_stats AS (
    SELECT
        cohort_year,
        0::INT AS year_index,
        COUNT(DISTINCT mobile) AS active_customers,
        SUM(total_spend)::FLOAT AS year_revenue
    FROM customer_cohorts
    GROUP BY cohort_year
    UNION ALL
    SELECT
        cohort_year,
        (EXTRACT(YEAR FROM lv_month) - EXTRACT(YEAR FROM fv_month))::INT AS year_index,
        COUNT(DISTINCT mobile) AS active_in_last_year,
        SUM(total_spend * 0.3)::FLOAT AS revenue_approx  -- repeat portion approx
    FROM customer_cohorts
    WHERE lv_month > fv_month
    GROUP BY cohort_year, year_index
),
base_size AS (
    SELECT cohort_year, active_customers AS initial_size
    FROM yearly_stats WHERE year_index = 0
),
otb AS (
    SELECT cohort_year, COUNT(*) AS one_time_buyers
    FROM customer_cohorts WHERE visits = 1
    GROUP BY cohort_year
),
nrp AS (
    SELECT cohort_year, COUNT(*) AS no_return_purchases
    FROM customer_cohorts WHERE lv_month = fv_month
    GROUP BY cohort_year
)
SELECT
    s.cohort_year,
    s.year_index,
    s.active_customers,
    s.year_revenue,
    b.initial_size,
    (s.active_customers * 100.0 / NULLIF(b.initial_size, 0)) AS retention_rate,
    COALESCE(o.one_time_buyers, 0) AS one_time_buyers,
    COALESCE(n.no_return_purchases, 0) AS no_return_purchases
FROM yearly_stats s
JOIN base_size b ON s.cohort_year = b.cohort_year
LEFT JOIN otb o ON s.cohort_year = o.cohort_year
LEFT JOIN nrp n ON s.cohort_year = n.cohort_year
ORDER BY s.cohort_year DESC, s.year_index ASC
WITH DATA
""", "mv_yearly_cohort created")

run("CREATE INDEX ON mv_yearly_cohort(cohort_year, year_index)", "index created")

# Verify
with connection.cursor() as cur:
    cur.execute("SELECT cohort_year, year_index, active_customers, initial_size, retention_rate FROM mv_yearly_cohort ORDER BY cohort_year, year_index")
    rows = cur.fetchall()
    print(f"  Rows: {len(rows)}")
    for r in rows[:10]:
        print(f"  {r[0]} yr{r[1]}: active={r[2]:,}  size={r[3]:,}  ret={r[4]:.1f}%")

# ─────────────────────────────────────────────────────────────────────────────
# MV 2: mv_cohort_rfm — RFM segment counts per cohort year
# ─────────────────────────────────────────────────────────────────────────────
print("\nCreating mv_cohort_rfm...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_cohort_rfm CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_cohort_rfm AS
WITH parsed AS (
    SELECT
        cd.mobile,
        EXTRACT(YEAR FROM cd.fv_month)::TEXT AS cohort_year,
        cs.visits,
        CASE
            WHEN cs.last_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                THEN SUBSTRING(cs.last_visit, 1, 10)::DATE
            WHEN cs.last_visit ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}'
                THEN TO_DATE(cs.last_visit, 'DD-MM-YYYY')
            ELSE NULL
        END AS last_d
    FROM mv_customer_dates cd
    JOIN mv_customer_summary cs ON cs.mobile = cd.mobile
    WHERE cd.fv_month IS NOT NULL
),
segmented AS (
    SELECT
        cohort_year,
        CASE
            WHEN (CURRENT_DATE - last_d) <= 90  AND visits >= 3 THEN 'Champions'
            WHEN (CURRENT_DATE - last_d) <= 180 AND visits >= 2 THEN 'Loyal'
            WHEN (CURRENT_DATE - last_d) >  365                 THEN 'Lost'
            ELSE 'Average'
        END AS segment
    FROM parsed
    WHERE last_d IS NOT NULL
)
SELECT cohort_year, segment, COUNT(*)::BIGINT AS customer_count
FROM segmented
GROUP BY cohort_year, segment
ORDER BY cohort_year, segment
WITH DATA
""", "mv_cohort_rfm created")

run("CREATE INDEX ON mv_cohort_rfm(cohort_year)", "index created")

with connection.cursor() as cur:
    cur.execute("SELECT cohort_year, segment, customer_count FROM mv_cohort_rfm ORDER BY cohort_year, segment")
    rows = cur.fetchall()
    print(f"  Rows: {len(rows)}")
    for r in rows[:8]:
        print(f"  {r[0]} {r[1]}: {r[2]:,}")

# ─────────────────────────────────────────────────────────────────────────────
# MV 3: mv_cohort_retention — monthly cohort heatmap matrix
# Build from mv_customer_dates using fv_month as cohort + lv_month as last active
# This is an approximation but avoids the massive raw scan.
# ─────────────────────────────────────────────────────────────────────────────
print("\nCreating mv_cohort_retention...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_cohort_retention CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_cohort_retention AS
WITH cohort_base AS (
    SELECT
        TO_CHAR(fv_month, 'YYYY-MM') AS cohort_month,
        COUNT(DISTINCT mobile)::BIGINT AS cohort_size
    FROM mv_customer_dates
    WHERE fv_month IS NOT NULL
    GROUP BY TO_CHAR(fv_month, 'YYYY-MM')
),
-- Month 0: everyone in their cohort month
month_zero AS (
    SELECT
        TO_CHAR(fv_month, 'YYYY-MM') AS cohort_month,
        0 AS month_number,
        COUNT(DISTINCT mobile)::BIGINT AS num_users
    FROM mv_customer_dates
    WHERE fv_month IS NOT NULL
    GROUP BY TO_CHAR(fv_month, 'YYYY-MM')
),
-- Month N: customers who returned (lv_month > fv_month, use diff in months)
return_months AS (
    SELECT
        TO_CHAR(fv_month, 'YYYY-MM') AS cohort_month,
        (
            (EXTRACT(YEAR FROM lv_month)::INT * 12 + EXTRACT(MONTH FROM lv_month)::INT)
            - (EXTRACT(YEAR FROM fv_month)::INT * 12 + EXTRACT(MONTH FROM fv_month)::INT)
        ) AS month_number,
        COUNT(DISTINCT mobile)::BIGINT AS num_users
    FROM mv_customer_dates
    WHERE fv_month IS NOT NULL AND lv_month > fv_month
    GROUP BY cohort_month, month_number
)
SELECT cohort_month, month_number, num_users FROM month_zero
UNION ALL
SELECT cohort_month, month_number, num_users FROM return_months
ORDER BY cohort_month, month_number
WITH DATA
""", "mv_cohort_retention created")

run("CREATE INDEX ON mv_cohort_retention(cohort_month, month_number)", "index created")

with connection.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM mv_cohort_retention")
    total = cur.fetchone()[0]
    cur.execute("SELECT cohort_month, month_number, num_users FROM mv_cohort_retention ORDER BY cohort_month, month_number LIMIT 10")
    rows = cur.fetchall()
    print(f"  Total rows: {total:,}")
    for r in rows:
        print(f"  {r[0]} m{r[1]}: {r[2]:,}")

# ─────────────────────────────────────────────────────────────────────────────
# Benchmark all three
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Benchmark ===")
for mv, sql in [
    ("mv_yearly_cohort",   "SELECT * FROM mv_yearly_cohort"),
    ("mv_cohort_rfm",      "SELECT * FROM mv_cohort_rfm"),
    ("mv_cohort_retention","SELECT * FROM mv_cohort_retention"),
]:
    t0 = time.time()
    with connection.cursor() as cur:
        for _ in range(3):
            cur.execute(sql)
            cur.fetchall()
    avg = (time.time() - t0) / 3 * 1000
    print(f"  {mv}: {avg:.1f}ms avg")

# Warm Django cache now
from django.core.cache import cache
from analytics.services import _q

print("\nWarming Django cache...")
# yearly cohort
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
print(f"  yearly_cohort cached: {len(cohort_data)} years")

# monthly cohort retention
rows = _q("SELECT cohort_month, month_number, num_users FROM mv_cohort_retention ORDER BY cohort_month, month_number")
cohorts = {}
for row in rows:
    c_month, m_num, count = row
    if c_month not in cohorts:
        cohorts[c_month] = {}
    cohorts[c_month][m_num] = count
cache.set('cohort_retention_global', {'cohorts': cohorts}, 86400)
print(f"  cohort_retention cached: {len(cohorts)} cohort months")

print("\nDone! Both caches are warm. Next load will be instant.")
