import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.core.cache import cache
from django.db import connection

# Clear Django cache
cache.clear()
print("Django cache cleared.")

# Kill active REFRESH queries to unblock the dashboard
cur = connection.cursor()
cur.execute("""
    SELECT pg_cancel_backend(pid)
    FROM pg_stat_activity 
    WHERE query LIKE 'REFRESH MATERIALIZED VIEW%' 
      AND state = 'active'
""")
killed = cur.fetchall()
print(f"Killed {len(killed)} hanging refresh queries.")
