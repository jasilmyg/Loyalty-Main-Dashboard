import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

sql = """
    SELECT MAX(parsed_date) 
    FROM sales_data 
    WHERE parsed_date >= '2026-07-01' AND parsed_date <= '2026-07-31';
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    row = cursor.fetchone()
    if row and row[0]:
        print(f"LAST DATE OF DATA IN JULY 2026: {row[0]}")
    else:
        print("NO DATA FOUND FOR JULY 2026")
