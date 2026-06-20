import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    cur.execute("""
        SELECT pid, now() - query_start AS duration, left(query, 80) AS query_snippet, state
        FROM pg_stat_activity
        WHERE (now() - query_start) > interval '3 minutes'
        AND state != 'idle'
        ORDER BY duration DESC;
    """)
    rows = cur.fetchall()
    if rows:
        print(f"Found {len(rows)} long-running queries - terminating:")
        for r in rows:
            print(f"  PID {r[0]} | {str(r[1]).split('.')[0]} | {r[3]} | {r[2]}")
        for r in rows:
            try:
                cur.execute("SELECT pg_terminate_backend(%s)", [r[0]])
                print(f"  -> Terminated PID {r[0]}")
            except Exception as e:
                print(f"  -> Could not terminate {r[0]}: {e}")
    else:
        print("No long-running queries found - DB is clear.")
