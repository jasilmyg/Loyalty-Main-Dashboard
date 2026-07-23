import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
WITH Q2_2025_HighSpenders AS (
    SELECT "Customer Mobile", SUM("Total Value") as total_spend
    FROM sales_data
    WHERE parsed_date >= '2025-04-01' AND parsed_date <= '2025-06-30'
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
    GROUP BY "Customer Mobile"
    HAVING SUM("Total Value") > 100000
),
Customers_After_Q2_2025 AS (
    SELECT DISTINCT "Customer Mobile"
    FROM sales_data
    WHERE parsed_date > '2025-06-30'
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
)
SELECT 
    (SELECT COUNT(*) FROM Q2_2025_HighSpenders) as total_high_spenders,
    (SELECT COUNT(*) FROM Q2_2025_HighSpenders WHERE "Customer Mobile" NOT IN (SELECT "Customer Mobile" FROM Customers_After_Q2_2025)) as lost_customers;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    if row:
        print(f"Total High Spenders in Q2 2025: {row[0]}")
        print(f"High Spenders who NEVER returned after Q2 2025: {row[1]}")
    else:
        print("No data found.")
