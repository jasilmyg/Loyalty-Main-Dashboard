"""
Step 1: Delete ALL 1970-01-01 rows from both tables (bad data from failed Aug 19 load).
Step 2: Re-load Aug 19 data with EXPLICIT column mapping so the right CSV columns
        go into the right ClickHouse columns.
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

SAS_TOKEN = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL = "https://stmygoalposreports.blob.core.windows.net"
CONN = f"BlobEndpoint={ACCOUNT_URL}/;SharedAccessSignature={SAS_TOKEN}"

INV_BLOB  = 'invoice_wise_sales_report/invoice_wise_sales_report_20-08-2026_03_00_03_486245.csv'
SALES_BLOB = 'item_wise_sales_report/item_wise_sales_report_20-08-2026_03_00_01_867102.csv'

# ── STEP 1: Delete bad 1970-01-01 rows from both tables ─────────────────────
print("STEP 1: Deleting 1970-01-01 rows from azure_invoice_report...")
ch.command("ALTER TABLE azure_invoice_report DELETE WHERE toDate(date) = '1970-01-01'")
print("  Delete mutation issued for azure_invoice_report.")

print("STEP 1: Deleting 1970-01-01 rows from azure_sales_report...")
ch.command("ALTER TABLE azure_sales_report DELETE WHERE toDate(date) = '1970-01-01'")
print("  Delete mutation issued for azure_sales_report.")

# Also delete any duplicate Aug 19 rows that may have been inserted incorrectly
print("STEP 1: Deleting any existing Aug 19 rows (to avoid duplicates)...")
ch.command("ALTER TABLE azure_invoice_report DELETE WHERE toDate(date) = '2026-08-19'")
ch.command("ALTER TABLE azure_sales_report DELETE WHERE toDate(date) = '2026-08-19'")
print("  Cleanup done.")

# ── STEP 2: Re-load Aug 19 with EXPLICIT column mapping ─────────────────────
# CSV invoice columns: Date, Time, Invoice No, Branch, RBM, BDM,
#   Customer Bill To No, Customer Bill To Pincode, Customer Bill to GSTIN,
#   Customer Type, Sales Staff Code, Billing Staff Code, Invoice Total,
#   Discount, Buyback, Deductions (Indirect), Exchange,
#   Financier Code, Financier Name, Scheme, Loan Amount
#
# Table columns:      date, time, invoice_no, branch, rbm, bdm,
#   customer_mobile, customer_pincode, customer_gstin,
#   customer_type, sales_staff_code, billing_staff_code, invoice_total,
#   discount, buyback, deductions, exchange,
#   financier_code, financier_name, scheme, loan_amount

print("\nSTEP 2: Loading Aug 19 invoice data with explicit column mapping...")
q_inv = f"""
INSERT INTO azure_invoice_report
(date, time, invoice_no, branch, rbm, bdm,
 customer_mobile, customer_pincode, customer_gstin,
 customer_type, sales_staff_code, billing_staff_code, invoice_total,
 discount, buyback, deductions, exchange,
 financier_code, financier_name, scheme, loan_amount)
SELECT
    Date,
    Time,
    `Invoice No`,
    Branch,
    RBM,
    BDM,
    toString(ifNull(`Customer Bill To No`, 0)),
    toString(ifNull(`Customer Bill To Pincode`, 0)),
    ifNull(`Customer Bill to GSTIN`, ''),
    ifNull(`Customer Type`, ''),
    ifNull(`Sales Staff Code`, ''),
    ifNull(`Billing Staff Code`, ''),
    ifNull(`Invoice Total`, 0),
    ifNull(Discount, 0),
    ifNull(Buyback, 0),
    ifNull(`Deductions (Indirect)`, 0),
    ifNull(Exchange, 0),
    ifNull(`Financier Code`, ''),
    ifNull(`Financier Name`, ''),
    ifNull(Scheme, ''),
    ifNull(`Loan Amount`, 0)
FROM azureBlobStorage(
    '{CONN}',
    'sales-reports',
    '{INV_BLOB}',
    'CSVWithNames'
)
WHERE Branch NOT IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
  AND `Invoice No` NOT LIKE '%SMC%'
  AND `Invoice No` NOT LIKE '%EI%'
"""
try:
    ch.command(q_inv)
    print("  SUCCESS: Aug 19 invoice data loaded.")
except Exception as e:
    print(f"  ERROR loading invoice data: {e}")

# CSV sales columns: Date, Invoice No, Branch, Item Code, IMEI/Batch,
#   Qty, MOP, Discount, Buyback, Sold Price, Taxable
#
# Table columns:     date, invoice_no, branch, item_code, imei_batch,
#   qty, mop, discount, buyback, sold_price, taxable

print("\nSTEP 2: Loading Aug 19 sales data with explicit column mapping...")
q_sales = f"""
INSERT INTO azure_sales_report
(date, invoice_no, branch, item_code, imei_batch,
 qty, mop, discount, buyback, sold_price, taxable)
SELECT
    Date,
    `Invoice No`,
    Branch,
    `Item Code`,
    `IMEI/Batch`,
    ifNull(Qty, 0),
    ifNull(MOP, 0),
    ifNull(Discount, 0),
    ifNull(Buyback, 0),
    ifNull(`Sold Price`, 0),
    ifNull(Taxable, 0)
FROM azureBlobStorage(
    '{CONN}',
    'sales-reports',
    '{SALES_BLOB}',
    'CSVWithNames'
)
WHERE Branch NOT IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
  AND `Invoice No` NOT LIKE '%SMC%'
  AND `Invoice No` NOT LIKE '%EI%'
"""
try:
    ch.command(q_sales)
    print("  SUCCESS: Aug 19 sales data loaded.")
except Exception as e:
    print(f"  ERROR loading sales data: {e}")

# ── STEP 3: Verify ───────────────────────────────────────────────────────────
import time
print("\nWaiting 5s for mutations to progress...")
time.sleep(5)

print("\nSTEP 3: Verification...")
r = ch.query("SELECT toDate(date), count() FROM azure_invoice_report WHERE toDate(date) = '2026-08-19'").result_rows[0]
print(f"  azure_invoice_report  Aug 19 rows = {r[1]}")

r = ch.query("SELECT toDate(date), count() FROM azure_sales_report WHERE toDate(date) = '2026-08-19'").result_rows[0]
print(f"  azure_sales_report    Aug 19 rows = {r[1]}")

r = ch.query("SELECT toDate(max(date)) FROM azure_invoice_report WHERE toDate(date) != '1970-01-01'").result_rows[0]
print(f"  azure_invoice_report  max date    = {r[0]}")

r = ch.query("SELECT toDate(max(date)) FROM azure_sales_report WHERE toDate(date) != '1970-01-01'").result_rows[0]
print(f"  azure_sales_report    max date    = {r[0]}")

print("\nDone! Mutations for 1970-01-01 delete run asynchronously in background.")
