"""
Load DSR AUG 17-19 2026.xlsx into ClickHouse sales_data table.
Filters:
  - Remove Invoice Number containing 'SMC' or 'EI'
  - Remove Branch = 'HEAD OFFICE' or 'UG SMART CHOICE'
"""
import os, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

FILE = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR AUG 17-19 2026.xlsx"

# ── Step 1: Read Excel ────────────────────────────────────────────────────────
print("Reading Excel file...")
df = pd.read_excel(FILE, sheet_name='Detailed Sales Report')
print(f"  Total rows: {len(df):,}")

# ── Step 2: Filter ────────────────────────────────────────────────────────────
print("\nApplying filters...")
before = len(df)
df = df[~df['Invoice Number'].astype(str).str.contains('SMC|EI', case=False, na=False)]
print(f"  Removed SMC/EI invoices: {before - len(df):,} rows")

before = len(df)
df = df[~df['Branch'].astype(str).str.upper().isin(['HEAD OFFICE', 'UG SMART CHOICE'])]
print(f"  Removed excluded branches: {before - len(df):,} rows")
print(f"  Rows remaining: {len(df):,}")

# ── Step 3: Parse dates ───────────────────────────────────────────────────────
print("\nParsing dates...")
df['parsed_date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce').dt.date
df = df.dropna(subset=['parsed_date'])
print(f"  Dates: {sorted(df['parsed_date'].unique())}")

# ── Step 4: Clean & map to ClickHouse column names ───────────────────────────
def to_str(val, default=''):
    if pd.isna(val) or val is None:
        return default
    return str(val).strip()

def to_num_str(val):
    if pd.isna(val) or val is None:
        return '0'
    try:
        f = float(val)
        return str(int(f)) if f == int(f) else str(f)
    except:
        return str(val).strip()

print("\nBuilding rows for insert...")
rows_to_insert = []
for _, row in df.iterrows():
    # Customer Mobile: strip .0 suffix from int-read values
    mob = to_str(row.get('Customer Mobile', ''))
    if mob.endswith('.0'):
        mob = mob[:-2]

    rows_to_insert.append({
        'Slno':              to_str(row.get('Slno', '')),
        'Date':              to_str(row.get('Date', '')),
        'Time':              to_str(row.get('Time', '')),
        'invoice_number':    to_str(row.get('Invoice Number', '')),
        'enq_job_no':        to_str(row.get('Enq/Job No.', '')),
        'RBM':               to_str(row.get('RBM', '')),
        'BDM':               to_str(row.get('BDM', '')),
        'branch':            to_str(row.get('Branch', '')),
        'staff_code':        to_str(row.get('Staff Code', '')),
        'staff':             to_str(row.get('Staff', '')),
        'customer_name':     to_str(row.get('Customer Name', '')),
        'customer_mobile':   mob,
        'financier':         to_str(row.get('Financier', '')),
        'finance':           to_num_str(row.get('Finance', 0)),
        'delivery_order_no': to_str(row.get('Delivery Order No.', '')),
        'cash':              to_num_str(row.get('Cash', 0)),
        'debit_card':        to_num_str(row.get('Debit Card', 0)),
        'credit_card':       to_num_str(row.get('Credit Card', 0)),
        'benow':             to_num_str(row.get('Benow', 0)),
        'advance_receipt':   to_num_str(row.get('Advance Receipt', 0)),
        'bharath_qr':        to_num_str(row.get('Bharath QR', 0)),
        'paytm_qr':          to_num_str(row.get('Paytm QR', 0)),
        'pine_labs_qr':      to_num_str(row.get('Pine Labs QR', 0)),
        'upi_cashback':      to_num_str(row.get('UPI Cashback', 0)),
        'card_reward':       to_num_str(row.get('Card Reward', 0)),
        'card_cashback':     to_num_str(row.get('Card Cashback', 0)),
        'gift_voucher':      to_num_str(row.get('Gift Voucher', 0)),
        'approved_credit':   to_num_str(row.get('Approved Credit', 0)),
        'EMI':               to_str(row.get('EMI', '')),
        'customer_type':     to_str(row.get('Customer Type', '')),
        'total_value':       float(row.get('Total Value', 0) or 0),
        'exchange':          to_num_str(row.get('Exchange', 0)),
        'discount':          to_num_str(row.get('Discount', 0)),
        'indirect_discount': to_num_str(row.get('Indirect Discount', 0)),
        'buyback':           to_num_str(row.get('Buyback', 0)),
        'addition':          to_num_str(row.get('Addition', 0)),
        'deduction':         to_num_str(row.get('Deduction', 0)),
        'point_redemption':  to_num_str(row.get('POINT REDUMPTION (DEDUCTION)', 0)),
        'myg_online_coupon': to_num_str(row.get('MYG ONLINE COUPON (DEDUCTION)', 0)),
        'source_file':       'DSR AUG 17-19 2026.xlsx',
        'parsed_date':       row['parsed_date'],
        'uid':               int(row.get('Slno', 0) or 0),
    })

print(f"  Prepared {len(rows_to_insert):,} rows.")

# ── Step 5: Delete existing Aug 17-19 rows to avoid duplicates ───────────────
print("\nDeleting existing Aug 17-19 rows from sales_data (mutation)...")
ch.command("ALTER TABLE sales_data DELETE WHERE parsed_date IN (toDate('2026-08-17'), toDate('2026-08-18'), toDate('2026-08-19'))")
print("  Mutation issued. Waiting 6s...")
import time
time.sleep(6)

# ── Step 6: Insert in chunks ──────────────────────────────────────────────────
CHUNK = 5000
total = 0
cols = list(rows_to_insert[0].keys())
# Convert list of dicts -> list of lists (clickhouse_connect requires row-oriented list-of-lists)
data_rows = [[row[c] for c in cols] for row in rows_to_insert]
print(f"\nInserting {len(data_rows):,} rows in chunks of {CHUNK}...")
for i in range(0, len(data_rows), CHUNK):
    chunk = data_rows[i:i+CHUNK]
    ch.insert('sales_data', chunk, column_names=cols)
    total += len(chunk)
    print(f"  {total:,} / {len(data_rows):,} inserted")

print(f"\nInsert complete: {total:,} rows.")

# ── Step 7: Verify ────────────────────────────────────────────────────────────
print("\n=== Verification ===")
rows = ch.query("""
    SELECT parsed_date, count()
    FROM sales_data
    WHERE parsed_date IN (toDate('2026-08-17'), toDate('2026-08-18'), toDate('2026-08-19'))
    GROUP BY parsed_date ORDER BY parsed_date ASC
""").result_rows
for r in rows:
    print(f"  {r[0]}  :  {r[1]:,} rows")

r = ch.query("SELECT max(parsed_date), count() FROM sales_data WHERE parsed_date != toDate('1970-01-01')").result_rows[0]
print(f"\n  sales_data  max date = {r[0]}")
print(f"  sales_data  total    = {r[1]:,} rows")
print("\nDone!")
