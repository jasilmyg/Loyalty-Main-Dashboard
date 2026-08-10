import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    cur.execute('SELECT COUNT(*) FROM sales_data WHERE "Uid" IS NOT NULL')
    filled = cur.fetchone()[0]
    total = 13134963
    print(f"Filled : {filled:,} / {total:,} ({filled/total*100:.1f}%)")
    print(f"Remaining: {total-filled:,} rows")
