import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

cur = connection.cursor()

# Get daily sales aggregated from actual data using parsed_date
cur.execute("""
    SELECT 
        parsed_date,
        COUNT(*) as transactions,
        SUM("Total Value") as total_revenue,
        COUNT(DISTINCT "Customer Mobile") as unique_customers
    FROM sales_data
    WHERE parsed_date IS NOT NULL
      AND parsed_date >= '2021-01-01'
    GROUP BY parsed_date
    ORDER BY parsed_date
    LIMIT 10;
""")
print("=== Sample daily aggregates ===")
for row in cur.fetchall():
    print(row)

# Get total date range in parsed_date
cur.execute("""
    SELECT MIN(parsed_date), MAX(parsed_date)
    FROM sales_data WHERE parsed_date IS NOT NULL;
""")
print("\n=== Parsed Date Range ===")
print(cur.fetchone())

# Check 2026 data
cur.execute("""
    SELECT COUNT(*), MIN(parsed_date), MAX(parsed_date)
    FROM sales_data
    WHERE parsed_date >= '2026-01-01';
""")
print("\n=== 2026 data ===")
print(cur.fetchone())
