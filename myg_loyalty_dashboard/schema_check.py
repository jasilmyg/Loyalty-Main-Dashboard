import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

cur = connection.cursor()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'sales_data';")
for row in cur.fetchall():
    print(row)
