import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

# O(N) query using a single pass over the database
sql = """
SELECT 
    SUM(CASE WHEN q2_spend > 100000 THEN 1 ELSE 0 END) as total_high_spenders,
    SUM(CASE WHEN q2_spend > 100000 AND max_date <= '2025-06-30' THEN 1 ELSE 0 END) as lost_customers
FROM (
    SELECT 
        "Customer Mobile",
        SUM(CASE WHEN parsed_date >= '2025-04-01' AND parsed_date <= '2025-06-30' THEN "Total Value" ELSE 0 END) as q2_spend,
        MAX(parsed_date) as max_date
    FROM sales_data
    WHERE "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
    GROUP BY "Customer Mobile"
) as customer_stats;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    if row:
        print(f"Total High Spenders in Q2 2025: {row[0]}")
        print(f"High Spenders who NEVER returned after Q2 2025: {row[1]}")
    else:
        print("No data found.")
