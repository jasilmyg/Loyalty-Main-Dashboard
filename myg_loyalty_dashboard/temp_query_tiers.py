import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
WITH CustomerTotals AS (
    SELECT 
        "Customer Mobile",
        SUM("Total Value") as total_spend
    FROM sales_data
    WHERE parsed_date >= '2026-04-01' AND parsed_date <= '2026-06-30'
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
    GROUP BY "Customer Mobile"
),
Tiers AS (
    SELECT 
        CASE 
            WHEN total_spend > 100000 THEN 'High Tier (> 1L)'
            WHEN total_spend >= 50000 AND total_spend <= 100000 THEN 'Mid Tier (50K - 1L)'
            ELSE 'Low Tier (< 50K)'
        END as tier,
        total_spend
    FROM CustomerTotals
)
SELECT 
    tier,
    COUNT(*) as customer_count,
    SUM(total_spend) as total_revenue
FROM Tiers
GROUP BY tier
ORDER BY 
    CASE tier
        WHEN 'High Tier (> 1L)' THEN 1
        WHEN 'Mid Tier (50K - 1L)' THEN 2
        WHEN 'Low Tier (< 50K)' THEN 3
    END;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    rows = cursor.fetchall()
    print("CUSTOMER TIERS Q2 2026:")
    print("-" * 50)
    for row in rows:
        print(f"Tier: {row[0]}")
        print(f"Customers: {row[1]:,}")
        print(f"Total Revenue: Rs {row[2]:,.2f}")
        print("-" * 50)
