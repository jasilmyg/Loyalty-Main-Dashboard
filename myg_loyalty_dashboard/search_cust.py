import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    cur.execute("SELECT \"Customer Mobile\", \"Date\" FROM sales_data WHERE \"Customer Mobile\" LIKE '%9738287053%';")
    print(cur.fetchall())
