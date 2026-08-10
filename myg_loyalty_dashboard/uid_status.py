import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    cur.execute("""
        SELECT pid, state, 
               now() - query_start AS elapsed,
               left(query, 120) AS query_snippet
        FROM pg_stat_activity
        WHERE query ILIKE '%sales_data%'
          AND state != 'idle'
          AND pid != pg_backend_pid()
    """)
    rows = cur.fetchall()
    if rows:
        print("=== Active sales_data queries ===")
        for r in rows:
            print(f"  PID     : {r[0]}")
            print(f"  State   : {r[1]}")
            print(f"  Elapsed : {r[2]}")
            print(f"  Query   : {r[3]}")
            print()
    else:
        print("No active query on sales_data found.")
        print("The UPDATE may have already completed or timed out.")
