"""
Create mv_loyalty_kpis - a tiny 1-row MV with pre-computed global loyalty KPIs:
- total_customers, repeat_customers, avg_gap_days

And mv_action_engine - a tiny 3-row MV with pre-computed action engine segments.

These replace the expensive GREATEST/LEAST + regex parse over 5.1M rows
that takes 40-54 seconds on cold start.
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

# ── mv_loyalty_kpis: 1-row MV with pre-computed global KPIs ─────────────────
print("Step 1: Creating mv_loyalty_kpis...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_loyalty_kpis CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_loyalty_kpis AS
WITH parsed AS (
    SELECT
        mobile,
        visits,
        CASE
            WHEN last_visit  ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN SUBSTRING(last_visit,  1, 10)::DATE
            WHEN last_visit  ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}' THEN TO_DATE(last_visit,  'DD-MM-YYYY')
            ELSE NULL
        END AS d_last,
        CASE
            WHEN first_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN SUBSTRING(first_visit, 1, 10)::DATE
            WHEN first_visit ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}' THEN TO_DATE(first_visit, 'DD-MM-YYYY')
            ELSE NULL
        END AS d_first
    FROM mv_customer_summary
)
SELECT
    COUNT(mobile)::BIGINT                           AS total_customers,
    COUNT(mobile) FILTER (WHERE visits > 1)::BIGINT AS repeat_customers,
    AVG(
        CASE WHEN visits > 1
                  AND GREATEST(d_last, d_first) IS NOT NULL
                  AND LEAST(d_last, d_first) IS NOT NULL
             THEN (GREATEST(d_last, d_first) - LEAST(d_last, d_first))::FLOAT / (visits - 1)
             ELSE NULL
        END
    )::FLOAT                                        AS avg_gap_days
FROM parsed
WITH DATA
""", "mv_loyalty_kpis created")

# Verify
with connection.cursor() as cur:
    cur.execute("SELECT * FROM mv_loyalty_kpis")
    row = cur.fetchone()
    print(f"  total={row[0]:,}  repeat={row[1]:,}  avg_gap={row[2]:.1f} days")

# ── mv_action_engine: 3-row MV for action engine segments ───────────────────
print("\nStep 2: Creating mv_action_engine...")
run("DROP MATERIALIZED VIEW IF EXISTS mv_action_engine CASCADE")
run("""
CREATE MATERIALIZED VIEW mv_action_engine AS
WITH parsed AS (
    SELECT
        mobile, visits, total_spend,
        CASE
            WHEN last_visit  ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN SUBSTRING(last_visit,  1, 10)::DATE
            WHEN last_visit  ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}' THEN TO_DATE(last_visit,  'DD-MM-YYYY')
            ELSE NULL
        END AS d_last,
        CASE
            WHEN first_visit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN SUBSTRING(first_visit, 1, 10)::DATE
            WHEN first_visit ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}' THEN TO_DATE(first_visit, 'DD-MM-YYYY')
            ELSE NULL
        END AS d_first
    FROM mv_customer_summary
),
cs AS (
    SELECT
        mobile, visits, total_spend,
        (CURRENT_DATE - GREATEST(d_last, d_first))::INT AS recency_days
    FROM parsed
    WHERE GREATEST(d_last, d_first) IS NOT NULL
)
SELECT 'Lapsing High Value'        AS segment, COUNT(*)::BIGINT AS customers, SUM(total_spend)::FLOAT AS revenue_at_risk,
       'Send Win-Back SMS with custom discount' AS action
FROM cs WHERE recency_days BETWEEN 90 AND 180 AND total_spend >= 10000
UNION ALL
SELECT 'Recently Active',          COUNT(*), SUM(total_spend), 'Nurture with product feedback loop'
FROM cs WHERE recency_days <= 30 AND visits = 1
UNION ALL
SELECT 'Frequent Shoppers at Risk', COUNT(*), SUM(total_spend), 'Trigger premium loyalty offer'
FROM cs WHERE recency_days BETWEEN 45 AND 90 AND visits >= 3
WITH DATA
""", "mv_action_engine created")

with connection.cursor() as cur:
    cur.execute("SELECT segment, customers, revenue_at_risk FROM mv_action_engine")
    rows = cur.fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]:,} customers  rev_at_risk={r[2]:,.0f}")

# ── Benchmark both ─────────────────────────────────────────────────────────
print("\nStep 3: Benchmark...")
import time
t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_loyalty_kpis")
        cur.fetchall()
elapsed = (time.time() - t0) / 5
print(f"  mv_loyalty_kpis avg: {elapsed*1000:.1f}ms  (was 54,000ms)")

t0 = time.time()
with connection.cursor() as cur:
    for _ in range(5):
        cur.execute("SELECT * FROM mv_action_engine")
        cur.fetchall()
elapsed = (time.time() - t0) / 5
print(f"  mv_action_engine avg: {elapsed*1000:.1f}ms  (was 39,000ms)")

print("\nDone!")
