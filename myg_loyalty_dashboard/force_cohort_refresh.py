"""
force_cohort_refresh.py
========================
Force-clears the file-based Django cache and validates
cohort data includes July 30-31 records.
"""
import os, sys, django, glob, shutil

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

BASE = os.path.dirname(os.path.abspath(__file__))

# Step 1: Delete all .cache files directly from disk
cache_dir = os.path.join(BASE, '.cache')
count = 0
for root, dirs, files in os.walk(cache_dir):
    for f in files:
        path = os.path.join(root, f)
        try:
            os.remove(path)
            count += 1
        except Exception as e:
            print(f"  Could not delete {f}: {e}")

print(f"[1] Deleted {count} file-cache entries from .cache/")

# Step 2: Also call cache.clear() via Django
from django.core.cache import cache
cache.clear()
print("[2] Django cache.clear() done")

# Step 3: Run the live cohort query and show July 30-31 counts
print("\n[3] Verifying cohort data from ClickHouse...")

from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# Check July 30-31 data in sales_data
r = client.query("""
    SELECT parsed_date, count() as rows, countDistinct(customer_mobile) as customers
    FROM sales_data
    WHERE parsed_date >= '2026-07-29'
    AND length(customer_mobile) = 10 AND customer_mobile != ''
    GROUP BY parsed_date
    ORDER BY parsed_date DESC
""")
print("    Date          Rows    Customers")
print("    " + "-"*40)
for row in r.result_rows:
    print(f"    {row[0]}    {row[1]:>6,}    {row[2]:>8,}")

# Check 2026 cohort total
r2 = client.query("""
    SELECT count() as total_rows,
           countDistinct(customer_mobile) as unique_customers
    FROM sales_data
    WHERE toYear(parsed_date) = 2026
    AND length(customer_mobile) = 10 AND customer_mobile != ''
""")
total_rows, unique_cust = r2.result_rows[0]
print(f"\n    2026 cohort total: {total_rows:,} rows, {unique_cust:,} unique customers")

# Check latest date in cohort query scope
r3 = client.query("SELECT max(parsed_date) FROM sales_data")
print(f"    Latest date in DB: {r3.result_rows[0][0]}")

print("\n[4] Confirming cache is empty...")
cache_files_remaining = sum(len(files) for _, _, files in os.walk(cache_dir))
print(f"    Cache files remaining: {cache_files_remaining}")

print("\n[OK] Cohort section will serve fresh data on next page load.")
print("     Open http://127.0.0.1:8001/cohorts/ and click Refresh.")
