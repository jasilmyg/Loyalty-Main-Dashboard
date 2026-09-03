import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# Today: 2026-08-31  →  2 years back = 2024-08-31
# Customers whose LAST purchase was on or before 2024-08-31 (no purchase in last 2 years)
CUTOFF = '2024-08-31'

print("=" * 60)
print("  INACTIVE CUSTOMERS — Last 2 Years (no purchase since Aug 31 2024)")
print("=" * 60)

# Count total
res = client.query(f"""
    SELECT
        count()                 AS total_inactive,
        min(last_purchase)      AS earliest_last_purchase,
        max(last_purchase)      AS latest_last_purchase
    FROM (
        SELECT
            customer_mobile,
            max(toDate(date))   AS last_purchase
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
    WHERE last_purchase <= toDate('{CUTOFF}')
""")
r = res.result_rows[0]
print(f"\n  Total Inactive Customers : {r[0]:>10,}")
print(f"  Earliest Last Purchase   : {r[1]}")
print(f"  Latest Last Purchase     : {r[2]}")
print(f"  Cutoff Date              : {CUTOFF}")
print("=" * 60)
