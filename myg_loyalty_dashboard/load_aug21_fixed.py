"""
Load Aug 21 blob data into ClickHouse azure tables with explicit column mapping.
"""
import os, django, time
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()

from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

SAS_TOKEN  = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL = "https://stmygoalposreports.blob.core.windows.net"
CONN        = "BlobEndpoint=" + ACCOUNT_URL + "/;SharedAccessSignature=" + SAS_TOKEN

INV_BLOB   = "invoice_wise_sales_report/invoice_wise_sales_report_21-08-2026_03_00_03_606604.csv"
SALES_BLOB = "item_wise_sales_report/item_wise_sales_report_21-08-2026_03_00_01_918381.csv"
DATE_STR   = "2026-08-21"

# ── STEP 0: Clean up any bad Aug 21 rows from earlier attempt ────────────────
print("STEP 0: Cleaning up any existing Aug 21 rows...")
ch.command("ALTER TABLE azure_invoice_report DELETE WHERE toDate(date) = '2026-08-21'")
ch.command("ALTER TABLE azure_sales_report   DELETE WHERE toDate(date) = '2026-08-21'")
print("  Cleanup mutations issued. Waiting 5s...")
time.sleep(5)

# ── STEP 1: Load invoice with explicit column mapping ────────────────────────
print("\nSTEP 1: Loading Aug 21 invoice data...")
q_inv = """
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
    '""" + CONN + """',
    'sales-reports',
    '""" + INV_BLOB + """',
    'CSVWithNames'
)
WHERE Branch NOT IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
  AND `Invoice No` NOT LIKE '%SMC%'
  AND `Invoice No` NOT LIKE '%EI%'
"""
try:
    t0 = time.time()
    ch.command(q_inv)
    print("  SUCCESS: Invoice data loaded in " + str(round(time.time()-t0,1)) + "s")
except Exception as e:
    print("  ERROR: " + str(e))

# ── STEP 2: Load sales with explicit column mapping ──────────────────────────
print("\nSTEP 2: Loading Aug 21 sales data...")
q_sales = """
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
    '""" + CONN + """',
    'sales-reports',
    '""" + SALES_BLOB + """',
    'CSVWithNames'
)
WHERE Branch NOT IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
  AND `Invoice No` NOT LIKE '%SMC%'
  AND `Invoice No` NOT LIKE '%EI%'
"""
try:
    t0 = time.time()
    ch.command(q_sales)
    print("  SUCCESS: Sales data loaded in " + str(round(time.time()-t0,1)) + "s")
except Exception as e:
    print("  ERROR: " + str(e))

# ── STEP 3: Verify ──────────────────────────────────────────────────────────
print("\nSTEP 3: Verifying row counts...")
time.sleep(3)
inv_cnt   = ch.query("SELECT count() FROM azure_invoice_report WHERE toDate(date) = '" + DATE_STR + "'").result_rows[0][0]
sales_cnt = ch.query("SELECT count() FROM azure_sales_report   WHERE toDate(date) = '" + DATE_STR + "'").result_rows[0][0]
max_inv   = ch.query("SELECT toDate(max(date)) FROM azure_invoice_report WHERE toDate(date) != '1970-01-01'").result_rows[0][0]
max_sales = ch.query("SELECT toDate(max(date)) FROM azure_sales_report   WHERE toDate(date) != '1970-01-01'").result_rows[0][0]
print("  azure_invoice_report Aug 21 rows : " + str(inv_cnt))
print("  azure_sales_report   Aug 21 rows : " + str(sales_cnt))
print("  azure_invoice_report max date    : " + str(max_inv))
print("  azure_sales_report   max date    : " + str(max_sales))
print("\nDone!")
