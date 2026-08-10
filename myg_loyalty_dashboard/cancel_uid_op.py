import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    # Cancel any running queries on sales_data
    cur.execute("""
        SELECT pg_cancel_backend(pid)
        FROM pg_stat_activity
        WHERE query ILIKE '%sales_data%'
          AND state != 'idle'
          AND pid != pg_backend_pid()
    """)
    cancelled = cur.fetchall()
    print(f"Cancelled {len(cancelled)} running query/queries on sales_data.")
    connection.commit()
