import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

TOTAL = 13134963
BATCH_SIZE = 200000

print("=== Fast Uid Fill (Range-based, no ctid scan) ===\n")

# First, find the min/max internal rowid approach using row_number
# Instead, we'll use a direct offset-free approach:
# Reset sequence to pick up from where we left off
with connection.cursor() as cur:
    cur.execute("SELECT MAX(\"Uid\") FROM sales_data")
    max_uid = cur.fetchone()[0] or 0
    cur.execute(f"SELECT setval('sales_data_uid_seq', {max(max_uid, 1)}, true)")
    connection.commit()
    print(f"Sequence reset. Starting from {max_uid + 1:,}\n")

# Use a single efficient UPDATE with a subquery using row_number
# This runs in one shot - fastest method
print("Running single-pass UPDATE (fastest method)...")
print("This will take 5-15 minutes. Dashboard stays UP.\n")

start = time.time()
with connection.cursor() as cur:
    # Set a long statement timeout for this operation
    cur.execute("SET statement_timeout = '1800000'")  # 30 minutes
    cur.execute("""
        UPDATE sales_data
        SET "Uid" = sub.new_uid
        FROM (
            SELECT ctid,
                   nextval('sales_data_uid_seq') AS new_uid
            FROM sales_data
            WHERE "Uid" IS NULL
        ) sub
        WHERE sales_data.ctid = sub.ctid
    """)
    rows = cur.rowcount
    connection.commit()

elapsed = time.time() - start
print(f"Done! Updated {rows:,} rows in {elapsed/60:.1f} minutes")

# Verify
with connection.cursor() as cur:
    cur.execute('SELECT COUNT(*), COUNT("Uid"), COUNT(DISTINCT "Uid") FROM sales_data')
    row = cur.fetchone()
    print(f"\n=== VERIFICATION ===")
    print(f"  Total rows     : {row[0]:,}")
    print(f"  Rows with Uid  : {row[1]:,}")
    print(f"  Unique Uid vals: {row[2]:,}")
    if row[0] == row[1] == row[2]:
        print("  SUCCESS: Every row has a unique Uid!")
    else:
        print(f"  WARNING: {row[0]-row[1]:,} rows still missing Uid!")
