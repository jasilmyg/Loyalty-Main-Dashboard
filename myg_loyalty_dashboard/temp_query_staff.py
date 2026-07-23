import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
SELECT 
    "Staff", 
    "Branch", 
    SUM("Total Value") as total_revenue
FROM sales_data
WHERE parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30'
AND "Staff" IS NOT NULL AND "Staff" != ''
GROUP BY "Staff", "Branch"
ORDER BY SUM("Total Value") DESC
LIMIT 5;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    rows = cursor.fetchall()
    for idx, row in enumerate(rows, 1):
        print(f'{idx}. Staff: {row[0]} | Branch: {row[1]} | Revenue: Rs {row[2]:,.2f}')
