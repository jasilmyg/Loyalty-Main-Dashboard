import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SELECT COUNT(*) FROM sales_data")
    print('sales_data count:', cursor.fetchone()[0])
    
    cursor.execute('SELECT COUNT(DISTINCT "Customer Mobile") FROM sales_data')
    print('total unique customers:', cursor.fetchone()[0])
    
    cursor.execute('SELECT COUNT(DISTINCT "Customer Mobile") FROM sales_data WHERE "Date" LIKE \'%04-2026\' OR "Date" LIKE \'%05-2026\'')
    print('AMJ unique customers so far:', cursor.fetchone()[0])
