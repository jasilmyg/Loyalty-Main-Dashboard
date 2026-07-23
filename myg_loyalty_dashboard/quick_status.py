import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT state, count(*) FROM pg_stat_activity WHERE query ILIKE 'refresh%' GROUP BY state;")
    rows = cur.fetchall()
    print('Active REFRESH queries:', rows)
    cur.execute("SELECT COUNT(*) FROM mv_monthly_summary WHERE month_date >= '2026-07-01';")
    print('mv_monthly_summary July rows:', cur.fetchone()[0])
