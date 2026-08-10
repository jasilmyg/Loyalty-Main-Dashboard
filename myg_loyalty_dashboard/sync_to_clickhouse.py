"""
Sync ALL columns from PostgreSQL sales_data to ClickHouse Cloud
---------------------------------------------------------------
Raw row-by-row sync. No aggregation. All 42 columns.
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection
from analytics.clickhouse_service import get_ch_client

BATCH_SIZE = 50_000

client = get_ch_client()
if not client:
    print("ERROR: Cannot connect to ClickHouse!")
    exit(1)

print("=" * 60)
print("  PostgreSQL -> ClickHouse Full Sync (All Columns)")
print("=" * 60)

with connection.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM sales_data WHERE parsed_date IS NOT NULL")
    total = cur.fetchone()[0]

print(f"\nTotal PG rows : {total:,}")
print(f"Batch size    : {BATCH_SIZE:,}")
print(f"Total batches : ~{(total // BATCH_SIZE) + 1}")

print("\nClearing ClickHouse table...")
client.command("TRUNCATE TABLE sales_data")

print("Starting sync...\n")
start_total = time.time()
offset = 0
total_inserted = 0
batch_num = 0

CH_COLS = [
    "slno", "sale_date_text", "sale_time", "invoice_number", "enq_job_no",
    "rbm", "bdm", "branch", "staff_code", "staff",
    "customer_name", "customer_mobile", "financier", "finance", "delivery_order_no",
    "cash", "debit_card", "credit_card", "benow", "advance_receipt",
    "bharath_qr", "paytm_qr", "pine_labs_qr", "upi_cashback", "card_reward",
    "card_cashback", "gift_voucher", "approved_credit", "emi", "customer_type",
    "total_value", "exchange", "discount", "indirect_discount", "buyback",
    "addition", "deduction", "point_redemption", "myg_online_coupon", "source_file",
    "parsed_date", "uid"
]

while True:
    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                COALESCE("Slno", ''),
                COALESCE("Date", ''),
                COALESCE("Time", ''),
                COALESCE("Invoice Number", ''),
                COALESCE("Enq/Job No.", ''),
                COALESCE("RBM", ''),
                COALESCE("BDM", ''),
                COALESCE("Branch", ''),
                COALESCE("Staff Code", ''),
                COALESCE("Staff", ''),
                COALESCE("Customer Name", ''),
                COALESCE("Customer Mobile", ''),
                COALESCE("Financier", ''),
                COALESCE("Finance", ''),
                COALESCE("Delivery Order No.", ''),
                COALESCE("Cash", ''),
                COALESCE("Debit Card", ''),
                COALESCE("Credit Card", ''),
                COALESCE("Benow", ''),
                COALESCE("Advance Receipt", ''),
                COALESCE("Bharath QR", ''),
                COALESCE("Paytm QR", ''),
                COALESCE("Pine Labs QR", ''),
                COALESCE("UPI Cashback", ''),
                COALESCE("Card Reward", ''),
                COALESCE("Card Cashback", ''),
                COALESCE("Gift Voucher", ''),
                COALESCE("Approved Credit", ''),
                COALESCE("EMI", ''),
                COALESCE("Customer Type", ''),
                COALESCE("Total Value"::float, 0.0),
                COALESCE("Exchange", ''),
                COALESCE("Discount", ''),
                COALESCE("Indirect Discount", ''),
                COALESCE("Buyback", ''),
                COALESCE("Addition", ''),
                COALESCE("Deduction", ''),
                COALESCE("POINT REDUMPTION (DEDUCTION)", ''),
                COALESCE("MYG ONLINE COUPON (DEDUCTION)", ''),
                COALESCE(source_file, ''),
                parsed_date,
                COALESCE("Uid", 0)
            FROM sales_data
            WHERE parsed_date IS NOT NULL
            ORDER BY parsed_date
            LIMIT {BATCH_SIZE} OFFSET {offset}
        """)
        rows = cur.fetchall()

    if not rows:
        break

    client.insert("sales_data", rows, column_names=CH_COLS)

    batch_num += 1
    total_inserted += len(rows)
    elapsed = time.time() - start_total
    rate = total_inserted / elapsed if elapsed > 0 else 1
    eta = (total - total_inserted) / rate if rate > 0 else 0

    print(
        f"Batch {batch_num:>3} | "
        f"+{len(rows):>6,} | "
        f"Total {total_inserted:>10,} | "
        f"{total_inserted/total*100:>5.1f}% | "
        f"ETA {eta/60:.1f}m"
    )
    offset += BATCH_SIZE

elapsed = time.time() - start_total
print(f"\nSync done! {total_inserted:,} rows in {elapsed/60:.1f} minutes")
ch_count = client.query("SELECT count() FROM sales_data").result_rows[0][0]
print(f"ClickHouse count: {ch_count:,}")
