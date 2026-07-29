import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client

client = get_ch_client()

print("Counting unique customers up to June 30, 2026 (base for JAS 10% target)...")
row = client.query("""
    SELECT uniqExact(customer_mobile) AS base_customers
    FROM sales_data
    WHERE parsed_date < toDate('2026-07-01')
      AND parsed_date != toDate('1970-01-01')
      AND length(customer_mobile) = 10
      AND customer_mobile != ''
""").result_rows

base = int(row[0][0])
target = round(base * 0.10)
print(f"\nBase customers (up to Jun 30, 2026) : {base:,}")
print(f"JAS Target (10% of base)            : {target:,}")
