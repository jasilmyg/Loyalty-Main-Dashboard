"""
Rebuild mv_yearly_cohort with REVENUE values.
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

print("Cancelling any running queries on mv_yearly_cohort...")
try:
    with connection.cursor() as cur:
        cur.execute("""
            SELECT pg_cancel_backend(pid)
            FROM pg_stat_activity
            WHERE query ILIKE '%mv_yearly_cohort%' OR query ILIKE '%mv_customer_yearly_revenue%'
              AND state != 'idle'
              AND pid != pg_backend_pid()
        """)
        killed = cur.fetchall()
        if killed:
            print(f"  Cancelled {len(killed)} query(ies)")
except Exception as e:
    pass

import time; time.sleep(1)

print("\nStep 1: Creating mv_customer_yearly_revenue (this takes ~2 mins)...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_customer_yearly_revenue CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_customer_yearly_revenue AS
SELECT 
    "Customer Mobile" AS mobile,
    EXTRACT(YEAR FROM "Date")::INT AS active_year,
    SUM("Total Value"::FLOAT) AS year_revenue
FROM v_sales_data
WHERE "Date" IS NOT NULL AND "Customer Mobile" ~ '^[0-9]{10}$'
GROUP BY "Customer Mobile", EXTRACT(YEAR FROM "Date")::INT
WITH DATA
""", "mv_customer_yearly_revenue created")

run("CREATE INDEX idx_mv_cyr_mobile ON mv_customer_yearly_revenue(mobile)", "index mobile")
run("CREATE INDEX idx_mv_cyr_active_year ON mv_customer_yearly_revenue(active_year)", "index active_year")

row = q1("SELECT COUNT(*) FROM mv_customer_yearly_revenue")
print(f"  Rows: {row[0]:,}")

print("\nStep 2: Building mv_yearly_cohort with revenue...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_cohort CASCADE")

run("""
CREATE MATERIALIZED VIEW mv_yearly_cohort AS
WITH
-- Cohort year: earliest year each customer was active (handles fv/lv swap)
cohort AS (
    SELECT
        a.mobile,
        MIN(a.active_year)::INT AS cohort_year
    FROM mv_customer_yearly_revenue a
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
        COUNT(DISTINCT a.mobile)::BIGINT       AS active_customers,
        SUM(a.year_revenue)::FLOAT             AS year_revenue
    FROM mv_customer_yearly_revenue a
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
        FROM mv_customer_yearly_revenue
        GROUP BY mobile
    ) cnt ON cnt.mobile = c.mobile
    WHERE cnt.yr_count = 1
    GROUP BY c.cohort_year
)
SELECT
    y.cohort_year,
    y.year_index,
    y.active_customers,
    y.year_revenue,
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

print("\nVerification:")
rows = q("SELECT cohort_year, year_index, active_customers, initial_size, year_revenue FROM mv_yearly_cohort ORDER BY cohort_year, year_index")
for r in rows:
    print(f"  {r[0]} yr{r[1]}: {int(r[2]):,}/{int(r[3]):,} | Rev: ₹{float(r[4] or 0):,.0f}")

cache.delete('yearly_cohort_global')
cache.delete('cohort_retention_global')
print("Cache cleared. Next request loads from MV.")
print("\nDone!")
