import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
SELECT COUNT(*) FROM (
    SELECT DISTINCT "Customer Mobile" FROM sales_data 
    WHERE parsed_date >= '2026-04-01' AND parsed_date <= '2026-04-30' 
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
    INTERSECT
    SELECT DISTINCT "Customer Mobile" FROM sales_data 
    WHERE parsed_date >= '2026-05-01' AND parsed_date <= '2026-05-31' 
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
    INTERSECT
    SELECT DISTINCT "Customer Mobile" FROM sales_data 
    WHERE parsed_date >= '2026-06-01' AND parsed_date <= '2026-06-30' 
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
) AS loyal_customers;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    print('HIGHLY LOYAL CUSTOMERS (Apr + May + Jun):', row[0] if row else 0)
