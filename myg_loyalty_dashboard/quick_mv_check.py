import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

with connection.cursor() as cur:
    # Check active refresh queries
    cur.execute("""
        SELECT query, state, now() - query_start AS duration
        FROM pg_stat_activity
        WHERE query ILIKE '%REFRESH MATERIALIZED VIEW%'
        AND state != 'idle';
    """)
    active = cur.fetchall()
    print(f"=== Active REFRESH queries: {len(active)} ===")
    for q, state, dur in active:
        print(f"  State: {state}, Duration: {dur}, Query: {q[:80]}")

    # Check July data in key MVs
    print("\n=== July Data in Key MVs ===")
    checks = [
        ("mv_monthly_summary",        "month_date"),
        ("mv_monthly_members",        "month_date"),
        ("mv_loyalty_kpis",           "month_date"),
        ("mv_monthly_retention_2026", "month_date"),
    ]
    for mv, col in checks:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {mv} WHERE {col} >= '2026-07-01';")
            cnt = cur.fetchone()[0]
            status = "✓ YES" if cnt > 0 else "✗ NO"
            print(f"  {status}  {mv}: {cnt} rows for July")
        except Exception as e:
            print(f"  ERR {mv}: {e}")
