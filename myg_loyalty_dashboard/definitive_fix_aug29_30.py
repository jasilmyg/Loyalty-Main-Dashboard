"""
DEFINITIVE FIX: Force UTC datetimes into ClickHouse
=====================================================
Root cause: clickhouse-connect on IST machine converts naive datetimes
  naive 2026-08-30 00:00:00  →  2026-08-29 18:30:00 UTC (IST-5:30h)

Real fix: pass datetime with explicit UTC timezone
  datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)  →  stored as 2026-08-30 00:00:00 UTC ✅
"""
import os, sys, io, django, time
import pandas as pd
from datetime import datetime, timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from analytics.clickhouse_service import get_ch_client
from azure.storage.blob import ContainerClient

ACCOUNT_NAME   = "stmygoalposreports"
CONTAINER_NAME = "sales-reports"
SAS_TOKEN      = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
container_url  = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}?{SAS_TOKEN}"
container_client = ContainerClient.from_container_url(container_url)
ch = get_ch_client()

SALES_TABLE   = "azure_sales_report"
INVOICE_TABLE = "azure_invoice_report"

SALES_COLS = ['date', 'invoice_no', 'branch', 'item_code', 'imei_batch',
              'qty', 'mop', 'discount', 'buyback', 'sold_price', 'taxable']
INV_COLS   = ['date', 'time', 'invoice_no', 'branch', 'rbm', 'bdm',
              'customer_mobile', 'customer_pincode', 'customer_gstin',
              'customer_type', 'sales_staff_code', 'billing_staff_code',
              'invoice_total', 'discount', 'buyback', 'deductions',
              'exchange', 'financier_code', 'financier_name', 'scheme', 'loan_amount']
INV_STR    = {'time', 'invoice_no', 'branch', 'rbm', 'bdm', 'customer_mobile',
              'customer_pincode', 'customer_gstin', 'customer_type',
              'sales_staff_code', 'billing_staff_code', 'financier_code', 'financier_name', 'scheme'}
INV_FLOAT  = {'invoice_total', 'discount', 'buyback', 'deductions', 'exchange', 'loan_amount'}

RENAME_ITEM = {
    'Date': 'date', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'Item Code': 'item_code',
    'IMEI/Batch': 'imei_batch', 'IMEI/Batch No': 'imei_batch',
    'Qty': 'qty', 'QTY': 'qty', 'Quantity': 'qty',
    'MOP': 'mop', 'Discount': 'discount', 'Buyback': 'buyback',
    'Sold Price': 'sold_price', 'Taxable': 'taxable'
}
RENAME_INV = {
    'Date': 'date', 'Time': 'time', 'Invoice No': 'invoice_no', 'Invoice No.': 'invoice_no',
    'Branch': 'branch', 'RBM': 'rbm', 'BDM': 'bdm',
    'Customer Bill To No': 'customer_mobile', 'Customer Bill To No.': 'customer_mobile',
    'Customer Bill To Pincode': 'customer_pincode',
    'Customer Bill To GSTIN': 'customer_gstin', 'Customer Bill to GSTIN': 'customer_gstin',
    'Customer Type': 'customer_type',
    'Sales Staff Code': 'sales_staff_code', 'Billing Staff Code': 'billing_staff_code',
    'Invoice Total': 'invoice_total', 'Discount': 'discount', 'Buyback': 'buyback',
    'Deductions (Indirect)': 'deductions', 'Exchange': 'exchange',
    'Financier Code': 'financier_code', 'Financier Name': 'financier_name',
    'Scheme': 'scheme', 'Loan Amount': 'loan_amount'
}

def safe_str(s):   return s.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
def safe_float(s): return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

def to_utc_dt(ts):
    """Convert pandas Timestamp (date only) → UTC-aware datetime at midnight UTC."""
    return datetime(ts.year, ts.month, ts.day, 0, 0, 0, tzinfo=timezone.utc)

def parse_dates(series):
    """Parse dd-mm-yyyy strings to pandas date only (no time, no tz)."""
    return pd.to_datetime(series, format='%d-%m-%Y', errors='coerce').dt.normalize()

# ── Step 1: Clean up everything wrong ─────────────────────────────────────────
print("=" * 65)
print("  STEP 1: Delete all rows for Aug 28 18:30, Aug 29, Aug 30")
print("=" * 65)

# The residual load stored Aug 29 blob data as 2026-08-28 18:30:00
# Need to remove those too — but be careful NOT to delete real Aug 28 data
print("  Checking current state:")
for d in ['2026-08-28', '2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    print(f"    {d}: sales={s:,}  invoices={i:,}")

# Check raw datetime values
raw_recent = ch.query("""
    SELECT date, count() as cnt
    FROM azure_sales_report
    WHERE date >= '2026-08-28 00:00:00'
    GROUP BY date ORDER BY date
""").result_rows
print("\n  Raw datetime values >= 2026-08-28:")
for r in raw_recent:
    print(f"    {r[0]}  count={r[1]:,}")

# Delete wrongly stored 18:30 UTC rows (IST midnight stored incorrectly)
print("  Deleting wrongly stored 18:30:00 UTC rows (toHour=18, toMinute=30)...")
ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE toHour(date)=18 AND toMinute(date)=30 AND toDate(date) >= '2026-08-28'")
ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE toHour(date)=18 AND toMinute(date)=30 AND toDate(date) >= '2026-08-28'")
# Also delete any Aug 29 / Aug 30 rows stored at midnight UTC (from previous fix attempts)
ch.command(f"ALTER TABLE {SALES_TABLE}   DELETE WHERE toDate(date) IN ('2026-08-29','2026-08-30')")
ch.command(f"ALTER TABLE {INVOICE_TABLE} DELETE WHERE toDate(date) IN ('2026-08-29','2026-08-30')")

print("  Waiting 25s for all deletes to propagate...")
time.sleep(25)

print("  State after cleanup:")
for d in ['2026-08-28', '2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    mark = '✅' if d == '2026-08-28' and s > 0 else ('✅' if s == 0 else '⚠️')
    print(f"    {mark} {d}: sales={s:,}  invoices={i:,}")

# ── Step 2: Reload Aug 28 if it got deleted ────────────────────────────────────
aug28_s = ch.query(f"SELECT count() FROM {SALES_TABLE} WHERE toDate(date)='2026-08-28'").result_rows[0][0]
if aug28_s < 60000:
    print(f"\n  ⚠️  Aug 28 count dropped to {aug28_s:,} — reloading from 29-08-2026 blob...")
    # Aug 28 data is in the 29-08-2026 blob
    AUG28_ITEM = 'item_wise_sales_report/item_wise_sales_report_29-08-2026_03_00_02_444257.csv'
    AUG28_INV  = 'invoice_wise_sales_report/invoice_wise_sales_report_29-08-2026_03_00_03_919584.csv'
    
    raw = container_client.get_blob_client(AUG28_ITEM).download_blob().readall()
    df28 = pd.read_csv(io.BytesIO(raw))
    df28.rename(columns={k: v for k, v in RENAME_ITEM.items() if k in df28.columns}, inplace=True)
    df28['date'] = parse_dates(df28['date'])
    df28['invoice_no'] = safe_str(df28['invoice_no'])
    df28['branch']     = safe_str(df28['branch'])
    df28['item_code']  = safe_str(df28['item_code'])
    if 'imei_batch' not in df28.columns: df28['imei_batch'] = ''
    df28['imei_batch'] = df28['imei_batch'].fillna('').astype(str)
    df28['qty']        = safe_float(df28['qty'])
    df28['mop']        = safe_float(df28['mop'])
    df28['discount']   = safe_float(df28['discount'])
    if 'buyback' not in df28.columns: df28['buyback'] = 0.0
    df28['buyback']    = safe_float(df28['buyback'])
    df28['sold_price'] = safe_float(df28['sold_price'])
    df28['taxable']    = safe_float(df28['taxable'])
    df28 = df28[SALES_COLS].dropna(subset=['date'])
    df28 = df28[df28['invoice_no'].str.strip() != '']
    rows28 = [(to_utc_dt(r.date), r.invoice_no, r.branch, r.item_code,
               r.imei_batch, r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
              for r in df28.itertuples(index=False)]
    ch.insert(SALES_TABLE, rows28, column_names=SALES_COLS)
    print(f"  ✅ Re-inserted {len(rows28):,} rows for Aug 28 → {SALES_TABLE}")

    raw = container_client.get_blob_client(AUG28_INV).download_blob().readall()
    df28i = pd.read_csv(io.BytesIO(raw))
    df28i.rename(columns={k: v for k, v in RENAME_INV.items() if k in df28i.columns}, inplace=True)
    for c in INV_COLS:
        if c not in df28i.columns: df28i[c] = '' if c in INV_STR else 0.0
    df28i['date'] = parse_dates(df28i['date'])
    for c in INV_STR:
        if c in df28i.columns: df28i[c] = safe_str(df28i[c])
    for c in INV_FLOAT:
        if c in df28i.columns: df28i[c] = safe_float(df28i[c])
    df28i = df28i[INV_COLS].dropna(subset=['date'])
    df28i = df28i[df28i['invoice_no'].str.strip() != '']
    rows28i = [(to_utc_dt(r.date), r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
                r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
                r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
                r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
                r.scheme, r.loan_amount)
               for r in df28i.itertuples(index=False)]
    ch.insert(INVOICE_TABLE, rows28i, column_names=INV_COLS)
    print(f"  ✅ Re-inserted {len(rows28i):,} rows for Aug 28 → {INVOICE_TABLE}")
    time.sleep(10)

# ── Step 3: Load Aug 29 and Aug 30 with explicit UTC datetimes ─────────────────
LOADS = [
    {
        'label':     'Aug 29, 2026',
        'item_blob': 'item_wise_sales_report/item_wise_sales_report_30-08-2026_03_00_02_812386.csv',
        'inv_blob':  'invoice_wise_sales_report/invoice_wise_sales_report_30-08-2026_03_00_03_821420.csv',
    },
    {
        'label':     'Aug 30, 2026',
        'item_blob': 'item_wise_sales_report/item_wise_sales_report_31-08-2026_03_00_02_751833.csv',
        'inv_blob':  'invoice_wise_sales_report/invoice_wise_sales_report_31-08-2026_03_00_04_179739.csv',
    },
]

for load in LOADS:
    print(f"\n{'='*65}")
    print(f"  Loading: {load['label']}")
    print(f"{'='*65}")

    # Sales
    raw = container_client.get_blob_client(load['item_blob']).download_blob().readall()
    df  = pd.read_csv(io.BytesIO(raw))
    df.rename(columns={k: v for k, v in RENAME_ITEM.items() if k in df.columns}, inplace=True)
    df['date'] = parse_dates(df['date'])
    df['invoice_no'] = safe_str(df['invoice_no'])
    df['branch']     = safe_str(df['branch'])
    df['item_code']  = safe_str(df['item_code'])
    if 'imei_batch' not in df.columns: df['imei_batch'] = ''
    df['imei_batch'] = df['imei_batch'].fillna('').astype(str)
    df['qty']        = safe_float(df['qty'])
    df['mop']        = safe_float(df['mop'])
    df['discount']   = safe_float(df['discount'])
    if 'buyback' not in df.columns: df['buyback'] = 0.0
    df['buyback']    = safe_float(df['buyback'])
    df['sold_price'] = safe_float(df['sold_price'])
    df['taxable']    = safe_float(df['taxable'])
    df = df[SALES_COLS].dropna(subset=['date'])
    df = df[df['invoice_no'].str.strip() != '']
    print(f"  Dates in CSV: {sorted(df['date'].dt.date.unique())}  Rows: {len(df):,}")

    # USE to_utc_dt → explicit UTC timezone → clickhouse-connect will NOT apply local TZ offset
    rows = [(to_utc_dt(r.date), r.invoice_no, r.branch, r.item_code,
             r.imei_batch, r.qty, r.mop, r.discount, r.buyback, r.sold_price, r.taxable)
            for r in df.itertuples(index=False)]
    print(f"  First datetime going to CH: {rows[0][0]}  (tzinfo={rows[0][0].tzinfo})")
    ch.insert(SALES_TABLE, rows, column_names=SALES_COLS)
    print(f"  ✅ Inserted {len(rows):,} → {SALES_TABLE}")

    # Invoices
    raw = container_client.get_blob_client(load['inv_blob']).download_blob().readall()
    df2 = pd.read_csv(io.BytesIO(raw))
    df2.rename(columns={k: v for k, v in RENAME_INV.items() if k in df2.columns}, inplace=True)
    for c in INV_COLS:
        if c not in df2.columns: df2[c] = '' if c in INV_STR else 0.0
    df2['date'] = parse_dates(df2['date'])
    for c in INV_STR:
        if c in df2.columns: df2[c] = safe_str(df2[c])
    for c in INV_FLOAT:
        if c in df2.columns: df2[c] = safe_float(df2[c])
    df2 = df2[INV_COLS].dropna(subset=['date'])
    df2 = df2[df2['invoice_no'].str.strip() != '']
    rows2 = [(to_utc_dt(r.date), r.time, r.invoice_no, r.branch, r.rbm, r.bdm,
              r.customer_mobile, r.customer_pincode, r.customer_gstin, r.customer_type,
              r.sales_staff_code, r.billing_staff_code, r.invoice_total, r.discount,
              r.buyback, r.deductions, r.exchange, r.financier_code, r.financier_name,
              r.scheme, r.loan_amount)
             for r in df2.itertuples(index=False)]
    ch.insert(INVOICE_TABLE, rows2, column_names=INV_COLS)
    print(f"  ✅ Inserted {len(rows2):,} → {INVOICE_TABLE}")

print("\n  Waiting 15s for inserts to commit...")
time.sleep(15)

# ── Final Verification ─────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("  FINAL VERIFICATION")
print("=" * 65)
for d in ['2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM {SALES_TABLE}   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM {INVOICE_TABLE} WHERE toDate(date)='{d}'").result_rows[0][0]
    mark = '✅' if s > 0 else '❌'
    print(f"  {mark} {d}:  sales={s:>8,}   invoices={i:>7,}")

print("\n  Raw datetime values stored:")
rv = ch.query("""
    SELECT date, count() cnt FROM azure_sales_report
    WHERE date >= '2026-08-28' GROUP BY date ORDER BY date
""").result_rows
for r in rv:
    print(f"    {r[0]}  count={r[1]:,}")

ms = ch.query(f"SELECT max(toDate(date)) FROM {SALES_TABLE}").result_rows[0][0]
mi = ch.query(f"SELECT max(toDate(date)) FROM {INVOICE_TABLE}").result_rows[0][0]
print(f"\n  Max date in {SALES_TABLE}   : {ms}")
print(f"  Max date in {INVOICE_TABLE} : {mi}")
print("=" * 65)
