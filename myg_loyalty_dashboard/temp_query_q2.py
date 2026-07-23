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
    COUNT(*) as purchase_count,
    SUM("Total Value") as total_spent
FROM sales_data 
WHERE parsed_date >= '2026-04-01' AND parsed_date <= '2026-06-30'
AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
GROUP BY "Customer Mobile"
HAVING COUNT(*) > 1
ORDER BY SUM("Total Value") DESC
LIMIT 1;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    if row:
        print(f'Customer: {row[0]}')
        print(f'Purchases: {row[1]}')
        print(f'Highest Total Spent: Rs {row[2]:,.2f}')
    else:
        print('No customers with more than one purchase.')
