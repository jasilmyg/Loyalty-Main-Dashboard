import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# JAS 2026 = July 1 - Sep 30 2026 (data available till July 31)
# Repeat Members = customers who bought in JAS 2026 AND had prior purchases before July 1, 2026
r = client.query("""
    WITH
    -- Customers who purchased in JAS 2026 (Jul 1 onwards)
    jas_customers AS (
        SELECT
            customer_mobile,
            sum(total_value)  AS jas_spend,
            count()           AS jas_visits
        FROM sales_data
        WHERE parsed_date >= toDate('2026-07-01')
          AND total_value > 0
          AND length(customer_mobile) = 10
          AND customer_mobile != ''
        GROUP BY customer_mobile
    ),
    -- Customers who had purchase history BEFORE JAS 2026
    prior_customers AS (
        SELECT DISTINCT customer_mobile
        FROM sales_data
        WHERE parsed_date < toDate('2026-07-01')
          AND length(customer_mobile) = 10
          AND customer_mobile != ''
    ),
    -- Repeat = in JAS + had prior history
    repeat_jas AS (
        SELECT j.customer_mobile, j.jas_spend, j.jas_visits
        FROM jas_customers j
        INNER JOIN prior_customers p ON j.customer_mobile = p.customer_mobile
    ),
    -- New = in JAS but NO prior history
    new_jas AS (
        SELECT j.customer_mobile, j.jas_spend, j.jas_visits
        FROM jas_customers j
        LEFT JOIN prior_customers p ON j.customer_mobile = p.customer_mobile
        WHERE p.customer_mobile = ''
    )

    SELECT
        'Repeat Members (JAS 2026)'          AS segment,
        count()                              AS customers,
        sum(jas_spend)                       AS total_revenue,
        avg(jas_spend)                       AS avg_spend,
        median(jas_spend)                    AS median_spend,
        avg(jas_visits)                      AS avg_visits,
        sum(jas_spend) / sum(jas_visits)     AS asp_per_visit,
        quantile(0.25)(jas_spend)            AS p25,
        quantile(0.75)(jas_spend)            AS p75
    FROM repeat_jas

    UNION ALL

    SELECT
        'New Members (JAS 2026)'             AS segment,
        count()                              AS customers,
        sum(jas_spend)                       AS total_revenue,
        avg(jas_spend)                       AS avg_spend,
        median(jas_spend)                    AS median_spend,
        avg(jas_visits)                      AS avg_visits,
        sum(jas_spend) / sum(jas_visits)     AS asp_per_visit,
        quantile(0.25)(jas_spend)            AS p25,
        quantile(0.75)(jas_spend)            AS p75
    FROM new_jas
""")

print("JAS 2026 (July) — Avg Spend by Member Type")
print("=" * 60)
print()

rows = r.result_rows
total_rev = sum(row[2] for row in rows)
total_cust = sum(row[1] for row in rows)

for row in rows:
    seg, custs, rev, avg_sp, med_sp, avg_vis, asp_txn, p25, p75 = row
    print(f"  Segment           : {seg}")
    print(f"  Customers         : {custs:,}  ({custs*100/total_cust:.1f}%)")
    print(f"  Total Revenue     : Rs. {rev:,.0f}  ({rev*100/total_rev:.1f}% of JAS revenue)")
    print(f"  Avg Spend         : Rs. {avg_sp:,.0f}")
    print(f"  Median Spend      : Rs. {med_sp:,.0f}")
    print(f"  Avg Visits in JAS : {avg_vis:.2f}")
    print(f"  ASP per Visit     : Rs. {asp_txn:,.0f}")
    print(f"  Spend P25 - P75   : Rs. {p25:,.0f}  —  Rs. {p75:,.0f}")
    print()

print("-" * 60)
print(f"  Combined Total    : {total_cust:,} customers  |  Rs. {total_rev:,.0f}")
print(f"  Overall Avg Spend : Rs. {total_rev/total_cust:,.0f} per customer")
