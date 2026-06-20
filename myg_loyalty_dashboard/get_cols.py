import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

cur = connection.cursor()

# Get columns
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='sales_data' ORDER BY column_name;")
cols = [r[0] for r in cur.fetchall()]
print("Columns:", cols)

# Get sample row
cur.execute("SELECT * FROM sales_data LIMIT 1;")
row = cur.fetchone()
print("Sample row:", row)
