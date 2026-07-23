import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("=== Terminating all active MV queries on DB ===")
with connection.cursor() as cur:
    cur.execute("""
        SELECT pid, query
        FROM pg_stat_activity
        WHERE (query ILIKE '%REFRESH MATERIALIZED VIEW%' OR query ILIKE '%CREATE MATERIALIZED VIEW%')
          AND state != 'idle'
          AND pid != pg_backend_pid();
    """)
    rows = cur.fetchall()
    if not rows:
        print("No active MV queries found. Nothing to terminate.")
    else:
        for pid, query in rows:
            print(f"Terminating PID {pid}: {query[:80]}...")
            cur.execute(f"SELECT pg_terminate_backend({pid});")
            result = cur.fetchone()[0]
            print(f"  -> Terminated: {result}")
print("Done.")
