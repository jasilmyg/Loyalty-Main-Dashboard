import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

query = """
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE state = 'active' 
  AND pid <> pg_backend_pid()
  AND state_change < current_timestamp - interval '1 minutes';
"""

with connection.cursor() as cursor:
    cursor.execute(query)
    results = cursor.fetchall()

print(f"Killed {len(results)} stuck queries.")
