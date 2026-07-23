import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
SELECT 
    "Customer Mobile",
    COUNT(DISTINCT "Branch") as branch_count,
    SUM("Total Value") as total_spend
FROM sales_data
WHERE parsed_date >= '2026-04-01' AND parsed_date <= '2026-06-30'
AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
GROUP BY "Customer Mobile"
ORDER BY COUNT(DISTINCT "Branch") DESC, SUM("Total Value") DESC
LIMIT 1;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    if row:
        print(f"Customer Mobile: {row[0]}")
        print(f"Different Branches Visited: {row[1]}")
        print(f"Total Spend: Rs {row[2]:,.2f}")
    else:
        print("No customers found.")
