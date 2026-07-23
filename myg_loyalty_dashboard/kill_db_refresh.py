import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

print("Killing all active REFRESH MATERIALIZED VIEW queries on PostgreSQL...")
with connection.cursor() as cur:
    # Find and cancel all active refresh queries
    cur.execute("""
        SELECT pg_cancel_backend(pid), pid, query
        FROM pg_stat_activity
        WHERE query ILIKE '%REFRESH MATERIALIZED VIEW%'
        AND state NOT IN ('idle')
        AND pid != pg_backend_pid();
    """)
    rows = cur.fetchall()
    if rows:
        for cancelled, pid, query in rows:
            print(f"  Cancelled PID {pid}: {query[:60]}... -> {cancelled}")
    else:
        print("  No active REFRESH queries found - DB is now free!")

print("\nChecking DB is now responsive...")
with connection.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM mv_customer_summary;")
    count = cur.fetchone()[0]
    print(f"mv_customer_summary row count: {count}")
    
    cur.execute("SELECT COUNT(*) FROM mv_monthly_summary WHERE month_date >= '2026-07-01';")
    july_count = cur.fetchone()[0]
    print(f"mv_monthly_summary July rows: {july_count}")

print("\nDone! Dashboard should now show data.")
