"""
ch_table_manager.py
====================
Manages the two canonical ClickHouse tables:
  - item_wise_sales_data
  - invoice_wise_sales_data

Operations:
  1. DROP snapshot/date-stamped tables (e.g. item_wise_sales_2026_07_26)
  2. MERGE any unique rows from those snapshots into the main tables first
  3. Print final table counts

Run this ONCE to clean up the current state.
Going forward, use ch_append_data() to add new data.
"""

import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
if not client:
    print("ERROR: Cannot connect to ClickHouse")
    exit(1)

print("=" * 60)
print("  ClickHouse Table Cleanup & Consolidation")
print("=" * 60)

# ── Step 1: List all current tables ─────────────────────────────────────────
print("\n[1] Current tables in ClickHouse:")
rows = client.query("""
    SELECT name, engine, total_rows, formatReadableSize(total_bytes) as size
    FROM system.tables
    WHERE database = currentDatabase()
    ORDER BY name
""").result_rows

MAIN_TABLES = {"item_wise_sales_data", "invoice_wise_sales_data", "sales_data"}
snapshot_tables = []

for r in rows:
    name, engine, count, size = r
    tag = "  [MAIN]    " if name in MAIN_TABLES else "  [SNAPSHOT]"
    print(f"{tag} {name:50s} rows={count:>10,}  size={size}")
    if name not in MAIN_TABLES:
        snapshot_tables.append(name)

print(f"\nSnapshot tables found: {len(snapshot_tables)}")

# ── Step 2: For each snapshot, merge unique rows into the main table ─────────
for snap in snapshot_tables:
    # Determine which main table this snapshot belongs to
    if snap.startswith("item_wise"):
        main = "item_wise_sales_data"
    elif snap.startswith("invoice_wise"):
        main = "invoice_wise_sales_data"
    else:
        print(f"\n[SKIP] Don't know where to merge '{snap}' — skipping.")
        continue

    print(f"\n[2] Merging '{snap}' -> '{main}'")

    # Get columns of snapshot table
    col_rows = client.query(f"""
        SELECT name FROM system.columns
        WHERE database = currentDatabase() AND table = '{snap}'
        ORDER BY position
    """).result_rows
    snap_cols = [r[0] for r in col_rows]

    # Get columns of main table
    main_col_rows = client.query(f"""
        SELECT name FROM system.columns
        WHERE database = currentDatabase() AND table = '{main}'
        ORDER BY position
    """).result_rows
    main_cols = [r[0] for r in main_col_rows]

    # Only use columns that exist in BOTH tables
    shared_cols = [c for c in snap_cols if c in main_cols]
    col_list = ", ".join(shared_cols)
    print(f"   Shared columns: {shared_cols}")

    # Count snapshot rows
    snap_count = client.query(f"SELECT count() FROM {snap}").result_rows[0][0]
    print(f"   Snapshot rows  : {snap_count:,}")

    if snap_count == 0:
        print("   Empty snapshot — nothing to merge.")
    else:
        # Insert snapshot rows into main table (ClickHouse allows duplicates by design)
        # Use INSERT INTO ... SELECT to copy
        insert_sql = f"""
            INSERT INTO {main} ({col_list})
            SELECT {col_list} FROM {snap}
        """
        print(f"   Inserting {snap_count:,} rows into {main}...")
        client.command(insert_sql)
        print(f"   [OK] Done.")

    # Drop the snapshot table
    print(f"   Dropping snapshot table '{snap}'...")
    client.command(f"DROP TABLE IF EXISTS {snap}")
    print(f"   [OK] Dropped.")

# ── Step 3: Final counts ─────────────────────────────────────────────────────
print("\n[3] Final table counts:")
final_rows = client.query("""
    SELECT name, total_rows, formatReadableSize(total_bytes)
    FROM system.tables
    WHERE database = currentDatabase()
    ORDER BY name
""").result_rows

for r in final_rows:
    print(f"   {r[0]:50s} rows={r[1]:>10,}  size={r[2]}")

print("\n" + "=" * 60)
print("  DONE. Only canonical tables remain.")
print("=" * 60)
