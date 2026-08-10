"""
deep_check_avggap.py
Verifies the avg_gap calculation logic in ClickHouse step by step.
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

print("=" * 65)
print("  Deep Check: AVG GAP Logic in ClickHouse sales_data")
print("=" * 65)

# ── 1. Sample 5 repeat customers and show their actual visits ─────────────────
print("\n[1] Sample repeat customers — actual visit dates & gaps:")
rows = client.query("""
    WITH daily_visits AS (
        SELECT customer_mobile AS mobile, parsed_date AS purchase_date
        FROM sales_data
        WHERE parsed_date != toDate('1970-01-01')
          AND length(customer_mobile) = 10
        GROUP BY customer_mobile, parsed_date
    ),
    visit_counts AS (
        SELECT mobile, COUNT() AS visits, min(purchase_date) AS first, max(purchase_date) AS last
        FROM daily_visits GROUP BY mobile
    ),
    repeat_customers AS (
        SELECT mobile FROM visit_counts WHERE visits BETWEEN 3 AND 6
        LIMIT 5
    )
    SELECT d.mobile, d.purchase_date
    FROM daily_visits d
    INNER JOIN repeat_customers r ON d.mobile = r.mobile
    ORDER BY d.mobile, d.purchase_date
""").result_rows

current_mobile = None
prev_date = None
for r in rows:
    mob, dt = r[0], r[1]
    if mob != current_mobile:
        current_mobile = mob
        prev_date = None
        print(f"\n  Customer: ****{mob[-4:]}")
    gap = (dt - prev_date).days if prev_date else "—"
    print(f"    {dt}  gap={gap} days")
    prev_date = dt

# ── 2. Distribution of actual per-customer avg_gap values ─────────────────────
print("\n\n[2] Distribution of per-customer avg_gap_days:")
rows = client.query("""
    WITH daily_visits AS (
        SELECT customer_mobile AS mobile, parsed_date AS purchase_date
        FROM sales_data
        WHERE parsed_date != toDate('1970-01-01')
          AND length(customer_mobile) = 10
        GROUP BY customer_mobile, parsed_date
    ),
    ranked AS (
        SELECT mobile, purchase_date,
               lagInFrame(purchase_date) OVER(PARTITION BY mobile ORDER BY purchase_date) AS prev_date
        FROM daily_visits
    ),
    gaps AS (
        SELECT mobile, dateDiff('day', prev_date, purchase_date) AS gap_days
        FROM ranked WHERE prev_date != toDate('1970-01-01')
    ),
    customer_avg_gaps AS (
        SELECT mobile, avg(gap_days) AS avg_gap_days
        FROM gaps GROUP BY mobile
    )
    SELECT
        CASE
            WHEN avg_gap_days <= 7    THEN '01. 0-7 days'
            WHEN avg_gap_days <= 30   THEN '02. 8-30 days'
            WHEN avg_gap_days <= 60   THEN '03. 31-60 days'
            WHEN avg_gap_days <= 90   THEN '04. 61-90 days'
            WHEN avg_gap_days <= 180  THEN '05. 91-180 days'
            WHEN avg_gap_days <= 365  THEN '06. 181-365 days'
            WHEN avg_gap_days <= 730  THEN '07. 1-2 years'
            WHEN avg_gap_days <= 1095 THEN '08. 2-3 years'
            WHEN avg_gap_days <= 1460 THEN '09. 3-4 years'
            ELSE                           '10. 4+ years'
        END AS bucket,
        count() AS customers,
        round(avg(avg_gap_days), 1) AS avg_in_bucket
    FROM customer_avg_gaps
    GROUP BY bucket ORDER BY bucket
""").result_rows

total_repeat = sum(r[1] for r in rows)
print(f"  {'Bucket':<18} {'Customers':>10}  {'%':>6}  avg_in_bucket")
print("  " + "-" * 55)
running_avg = 0
for r in rows:
    pct = r[1] / total_repeat * 100
    running_avg += (r[1] / total_repeat) * r[2]
    print(f"  {r[0]:<18} {r[1]:>10,}  {pct:>5.1f}%  {r[2]} days")
print(f"\n  Total repeat customers : {total_repeat:,}")
print(f"  Weighted avg gap       : {running_avg:.1f} days")

# ── 3. Check for anomalies (very large or zero gaps) ─────────────────────────
print("\n\n[3] Anomaly check — extreme gap values:")
rows = client.query("""
    WITH daily_visits AS (
        SELECT customer_mobile AS mobile, parsed_date AS purchase_date
        FROM sales_data
        WHERE parsed_date != toDate('1970-01-01')
          AND length(customer_mobile) = 10
        GROUP BY customer_mobile, parsed_date
    ),
    ranked AS (
        SELECT mobile, purchase_date,
               lagInFrame(purchase_date) OVER(PARTITION BY mobile ORDER BY purchase_date) AS prev_date
        FROM daily_visits
    ),
    gaps AS (
        SELECT mobile, dateDiff('day', prev_date, purchase_date) AS gap_days
        FROM ranked WHERE prev_date != toDate('1970-01-01')
    )
    SELECT
        countIf(gap_days = 0)         AS zero_gaps,
        countIf(gap_days < 0)         AS negative_gaps,
        countIf(gap_days > 1000)      AS gaps_over_1000d,
        countIf(gap_days > 2000)      AS gaps_over_2000d,
        min(gap_days)                 AS min_gap,
        max(gap_days)                 AS max_gap,
        round(avg(gap_days), 1)       AS avg_all_gaps,
        count()                       AS total_gap_records
    FROM gaps
""").result_rows[0]

print(f"  Total gap records      : {rows[7]:,}")
print(f"  Zero gaps (same day)   : {rows[0]:,}")
print(f"  Negative gaps          : {rows[1]:,}")
print(f"  Gaps > 1000 days       : {rows[2]:,}")
print(f"  Gaps > 2000 days       : {rows[3]:,}")
print(f"  Min gap                : {rows[4]} days")
print(f"  Max gap                : {rows[5]} days")
print(f"  Avg of ALL raw gaps    : {rows[6]} days")

# ── 4. Cross-check: span/(visits-1) method vs lagInFrame method ───────────────
print("\n\n[4] Cross-check: span/(visits-1) vs lagInFrame (on same 10k customers):")
rows = client.query("""
    WITH daily_visits AS (
        SELECT customer_mobile AS mobile, parsed_date AS purchase_date
        FROM sales_data
        WHERE parsed_date != toDate('1970-01-01')
          AND length(customer_mobile) = 10
        GROUP BY customer_mobile, parsed_date
        LIMIT 10000000
    ),
    visit_counts AS (
        SELECT mobile,
               COUNT() AS visits,
               min(purchase_date) AS first_visit,
               max(purchase_date) AS last_visit
        FROM daily_visits
        GROUP BY mobile
        HAVING visits > 1
    ),
    -- Method 1: span / intervals
    method1 AS (
        SELECT round(avg(dateDiff('day', first_visit, last_visit)::Float64 / (visits - 1)), 1) AS avg_gap
        FROM visit_counts
    ),
    -- Method 2: lagInFrame
    ranked AS (
        SELECT mobile, purchase_date,
               lagInFrame(purchase_date) OVER(PARTITION BY mobile ORDER BY purchase_date) AS prev_date
        FROM daily_visits
    ),
    gaps AS (
        SELECT mobile, dateDiff('day', prev_date, purchase_date) AS gap_days
        FROM ranked WHERE prev_date != toDate('1970-01-01')
    ),
    cust_gaps AS (
        SELECT mobile, avg(gap_days) AS avg_gap_days FROM gaps GROUP BY mobile
    ),
    method2 AS (
        SELECT round(avg(avg_gap_days), 1) AS avg_gap FROM cust_gaps
    )
    SELECT m1.avg_gap AS method1_span, m2.avg_gap AS method2_lagInFrame
    FROM method1 m1, method2 m2
""").result_rows[0]

print(f"  Method 1 (span/intervals) : {rows[0]} days")
print(f"  Method 2 (lagInFrame)     : {rows[1]} days")
if rows[0] == rows[1]:
    print("  => Both methods agree! Logic is consistent.")
else:
    diff = abs(float(rows[0]) - float(rows[1]))
    print(f"  => Difference: {diff:.1f} days")

print("\n" + "=" * 65)
print("  Deep check complete.")
print("=" * 65)
