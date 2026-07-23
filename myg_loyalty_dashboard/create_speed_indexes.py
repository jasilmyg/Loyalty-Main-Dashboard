import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

INDEXES = [
    # Most critical: parsed_date is the main filter column for ALL dashboard queries
    ('idx_sales_parsed_date',
     'CREATE INDEX IF NOT EXISTS idx_sales_parsed_date ON sales_data(parsed_date);'),

    # Composite: parsed_date + Branch — used by every branch-filtered query
    ('idx_sales_parsed_date_branch',
     'CREATE INDEX IF NOT EXISTS idx_sales_parsed_date_branch ON sales_data(parsed_date, "Branch");'),

    # Customer Mobile — used by loyalty/RFM queries
    ('idx_sales_mobile',
     'CREATE INDEX IF NOT EXISTS idx_sales_mobile ON sales_data("Customer Mobile");'),

    # Composite: mobile + parsed_date — used for per-customer history queries
    ('idx_sales_mobile_date',
     'CREATE INDEX IF NOT EXISTS idx_sales_mobile_date ON sales_data("Customer Mobile", parsed_date);'),
]

print("=" * 60)
print("  CREATING MISSING PERFORMANCE INDEXES")
print("=" * 60)

with connection.cursor() as cur:
    # First check what already exists
    cur.execute("SELECT indexname FROM pg_indexes WHERE tablename='sales_data';")
    existing = {r[0] for r in cur.fetchall()}
    print(f"\nExisting indexes on sales_data: {len(existing)}")
    for e in sorted(existing):
        print(f"  - {e}")

    print("\nCreating missing indexes...")
    for name, sql in INDEXES:
        if name in existing:
            print(f"  SKIP (exists): {name}")
            continue
        t0 = time.time()
        print(f"  Creating: {name} ...", end=" ", flush=True)
        try:
            cur.execute(sql)
            print(f"OK ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"FAILED: {e}")

print("\nDone! Run ANALYZE to update query planner stats...")
with connection.cursor() as cur:
    cur.execute("ANALYZE sales_data;")
    print("  ANALYZE complete.")
