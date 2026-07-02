import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT DISTINCT \"Branch\" FROM sales_data WHERE \"Branch\" ILIKE '%Falnir%';")
    print(cur.fetchall())
