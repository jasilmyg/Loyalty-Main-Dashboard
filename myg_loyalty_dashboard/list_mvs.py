import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT matviewname, definition FROM pg_matviews ORDER BY matviewname")
    rows = cur.fetchall()
print("Total MVs:", len(rows))
for r in rows:
    print("---", r[0])
    print(r[1][:400])
    print()
