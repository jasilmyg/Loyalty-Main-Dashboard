"""
ch_investigate_duplicates.py
==============================
Investigates duplicate rows in item_wise_sales_data:
  1. True duplicates = identical rows (same invoice_no + item_code + imei_batch + date)
  2. Normal multi-item = same invoice_no but different item_code (this is EXPECTED)
  3. Shows where duplicates came from
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
print("  Investigating item_wise_sales_data duplicates")
print("=" * 60)

# ── 1. Total row count ────────────────────────────────────────────────────────
total = client.query("SELECT count() FROM item_wise_sales_data").result_rows[0][0]
print(f"\n[1] Total rows in item_wise_sales_data: {total:,}")

# ── 2. Distinct rows (true unique combinations) ───────────────────────────────
distinct = client.query("""
    SELECT count() FROM (
        SELECT DISTINCT date, invoice_no, branch, item_code, imei_batch, qty, sold_price
        FROM item_wise_sales_data
    )
""").result_rows[0][0]
print(f"[2] Distinct rows (no duplicates)       : {distinct:,}")
print(f"    Duplicate rows to remove             : {total - distinct:,}")

# ── 3. Find invoices with duplicate items ─────────────────────────────────────
print("\n[3] Invoices with true duplicate items (exact same row repeated):")
dup_invoices = client.query("""
    SELECT
        invoice_no,
        item_code,
        imei_batch,
        date,
        branch,
        count() AS occurrences
    FROM item_wise_sales_data
    GROUP BY invoice_no, item_code, imei_batch, date, branch
    HAVING occurrences > 1
    ORDER BY occurrences DESC
    LIMIT 20
""").result_rows

if not dup_invoices:
    print("    No true duplicates found!")
else:
    print(f"    Found {len(dup_invoices)} duplicate groups (showing top 20):")
    print(f"    {'invoice_no':<25} {'item_code':<15} {'imei_batch':<20} {'date':<12} {'branch':<10} copies")
    print("    " + "-" * 90)
    for r in dup_invoices:
        print(f"    {str(r[0]):<25} {str(r[1]):<15} {str(r[2]):<20} {str(r[3]):<12} {str(r[4]):<10} {r[5]}")

# ── 4. Total duplicate count ──────────────────────────────────────────────────
total_dup_count = client.query("""
    SELECT sum(occurrences - 1) FROM (
        SELECT count() AS occurrences
        FROM item_wise_sales_data
        GROUP BY invoice_no, item_code, imei_batch, date, branch
        HAVING occurrences > 1
    )
""").result_rows[0][0]
print(f"\n[4] Total extra duplicate rows: {int(total_dup_count or 0):,}")

# ── 5. Multi-item invoices (NORMAL - same invoice_no, different items) ────────
print("\n[5] Sample invoices with multiple items (NORMAL behavior):")
multi_item = client.query("""
    SELECT invoice_no, count() as item_count, groupArray(item_code) as items
    FROM item_wise_sales_data
    WHERE date = '30-04-2026'
    GROUP BY invoice_no
    HAVING item_count > 1
    ORDER BY item_count DESC
    LIMIT 5
""").result_rows

for r in multi_item:
    print(f"    {r[0]:30s}  items={r[1]}  codes={list(r[2])[:5]}")

# ── 6. Check if duplicates came from snapshot merge ───────────────────────────
print("\n[6] Checking date range of duplicates:")
if dup_invoices:
    dup_inv_nos = [r[0] for r in dup_invoices[:10]]
    for inv in dup_inv_nos[:5]:
        rows = client.query(f"""
            SELECT date, invoice_no, item_code, imei_batch, qty, sold_price
            FROM item_wise_sales_data
            WHERE invoice_no = '{inv}'
            ORDER BY item_code
        """).result_rows
        print(f"\n    Invoice: {inv} ({len(rows)} rows total):")
        for r in rows:
            print(f"      date={r[0]}  item={r[2]}  imei={r[3]}  qty={r[4]}  price={r[5]}")

print("\n" + "=" * 60)
print("  Investigation complete.")
print("=" * 60)
