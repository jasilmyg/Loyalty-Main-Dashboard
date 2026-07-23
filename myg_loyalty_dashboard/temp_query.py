import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
SELECT COUNT(DISTINCT "Customer Mobile") 
FROM sales_data 
WHERE parsed_date >= '2026-05-01' AND parsed_date <= '2026-05-31' 
AND "Total Value" > 50000;
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    print('UNIQUE CUSTOMERS:', row[0] if row else 0)
