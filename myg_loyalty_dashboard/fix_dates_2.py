import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
with connection.cursor() as cur:
    cur.execute("UPDATE sales_data SET parsed_date = CAST(\"Date\" AS date) WHERE parsed_date IS NULL")
    print('Updated rows:', cur.rowcount)
    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_true_repeat_amj_2026")
    print('Refreshed mv_true_repeat_amj_2026')
