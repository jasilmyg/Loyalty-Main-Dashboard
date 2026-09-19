"""
ingest_sep15_16_2026.py
========================
Loads Sep 15 and Sep 16, 2026 data into both Azure ClickHouse tables.

File naming convention (file named DD+1 contains data for DD):
  - Sep 15 data is in files named 16-09-2026
  - Sep 16 data is in files named 17-09-2026

Targets:
  item_wise    -> azure_sales_report
  invoice_wise -> azure_invoice_report
"""
import os, sys, time, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
if not client:
    print("ERROR: Cannot connect to ClickHouse")
    sys.exit(1)

SAS_CONN = (
    "BlobEndpoint=https://stmygoalposreports.blob.core.windows.net/;"
    "SharedAccessSignature=sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z"
    "&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
)

# Files to ingest: (item_file, invoice_file, data_date_label)
# Convention: file named DD-09-2026 contains data for (DD-1)-09-2026
FILES = [
    (
        "item_wise_sales_report/item_wise_sales_report_16-09-2026_03_00_02_589932.csv",
        "invoice_wise_sales_report/invoice_wise_sales_report_16-09-2026_03_00_03_531370.csv",
        "Sep 15"
    ),
    (
        "item_wise_sales_report/item_wise_sales_report_17-09-2026_03_00_01_814492.csv",
        "invoice_wise_sales_report/invoice_wise_sales_report_17-09-2026_03_00_02_878918.csv",
        "Sep 16"
    ),
]

# Check ingestion log to avoid double-inserts
try:
    already_ingested = {row[0] for row in client.query("SELECT file_name FROM azure_ingestion_log").result_rows}
    print(f"Already ingested files: {len(already_ingested)}")
except Exception as e:
    print(f"Warning: could not read ingestion log: {e}")
    already_ingested = set()

print("\n" + "="*70)
print("  Ingesting Sep 15-16, 2026 into azure_sales_report & azure_invoice_report")
print("="*70)

for item_file, inv_file, label in FILES:
    print(f"\n--- Data for {label} ---")

    # ── azure_sales_report (item_wise) ────────────────────────────────────
    if item_file in already_ingested:
        print(f"  [SKIP] {item_file.split('/')[-1]} already ingested.")
    else:
        print(f"  [ITEM] Ingesting: {item_file.split('/')[-1]}")
        sql_item = f"""
        INSERT INTO azure_sales_report
        SELECT
            `Date` as date,
            `Invoice No` as invoice_no,
            `Branch` as branch,
            `Item Code` as item_code,
            `IMEI/Batch` as imei_batch,
            `Qty` as qty,
            `MOP` as mop,
            `Discount` as discount,
            `Buyback` as buyback,
            `Sold Price` as sold_price,
            `Taxable` as taxable
        FROM azureBlobStorage('{SAS_CONN}', 'sales-reports', '{item_file}', 'CSVWithNames')
        WHERE
            `Invoice No` NOT LIKE '%SMC%' AND
            `Invoice No` NOT LIKE '%EI%' AND
            `Branch` NOT IN ('HEAD OFFICE', 'UG SMART CHOICE')
        """
        try:
            t0 = time.time()
            client.command(sql_item)
            client.insert('azure_ingestion_log', [[item_file]], column_names=['file_name'])
            print(f"  [ITEM] Done in {time.time()-t0:.2f}s")
        except Exception as e:
            print(f"  [ITEM] ERROR: {e}")

    # ── azure_invoice_report (invoice_wise) ───────────────────────────────
    if inv_file in already_ingested:
        print(f"  [SKIP] {inv_file.split('/')[-1]} already ingested.")
    else:
        print(f"  [INV]  Ingesting: {inv_file.split('/')[-1]}")
        sql_inv = f"""
        INSERT INTO azure_invoice_report
        SELECT
            `Date` as date, `Time` as time, `Invoice No` as invoice_no,
            `Branch` as branch, `RBM` as rbm, `BDM` as bdm,
            `Customer Bill To No` as customer_mobile,
            `Customer Bill To Pincode` as customer_pincode,
            `Customer Bill to GSTIN` as customer_gstin,
            `Customer Type` as customer_type,
            `Sales Staff Code` as sales_staff_code,
            `Billing Staff Code` as billing_staff_code,
            `Invoice Total` as invoice_total,
            `Discount` as discount, `Buyback` as buyback,
            `Deductions (Indirect)` as deductions,
            `Exchange` as exchange,
            `Financier Code` as financier_code,
            `Financier Name` as financier_name,
            `Scheme` as scheme, `Loan Amount` as loan_amount
        FROM azureBlobStorage('{SAS_CONN}', 'sales-reports', '{inv_file}', 'CSVWithNames')
        WHERE
            `Invoice No` NOT LIKE '%SMC%' AND
            `Invoice No` NOT LIKE '%EI%' AND
            `Branch` NOT IN ('HEAD OFFICE', 'UG SMART CHOICE')
        """
        try:
            t0 = time.time()
            client.command(sql_inv)
            client.insert('azure_ingestion_log', [[inv_file]], column_names=['file_name'])
            print(f"  [INV]  Done in {time.time()-t0:.2f}s")
        except Exception as e:
            print(f"  [INV]  ERROR: {e}")

# ── Final verification ──────────────────────────────────────────────────────
print("\n" + "="*70)
print("  VERIFICATION: MAX dates and row counts after ingestion")
print("="*70)

for table in ['azure_sales_report', 'azure_invoice_report']:
    result = client.query(f"SELECT MIN(date), MAX(date), count() FROM {table}").result_rows[0]
    print(f"  {table}")
    print(f"    MIN={result[0]}  MAX={result[1]}  Rows={result[2]:,}")

# Per-day counts for the newly ingested dates
print("\n  Per-day row counts for Sep 15-16:")
for date_str in ['2026-09-15', '2026-09-16']:
    for table in ['azure_sales_report', 'azure_invoice_report']:
        cnt = client.query(f"SELECT count() FROM {table} WHERE date = '{date_str}'").result_rows[0][0]
        status = 'OK' if cnt > 0 else 'MISSING'
        print(f"    [{status}] {table} | {date_str}: {cnt:,} rows")

print("\nDone!")
