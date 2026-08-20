import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

SAS_TOKEN = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL = "https://stmygoalposreports.blob.core.windows.net"
CONN = f"BlobEndpoint={ACCOUNT_URL}/;SharedAccessSignature={SAS_TOKEN}"

INV_BLOB = 'invoice_wise_sales_report/invoice_wise_sales_report_20-08-2026_03_00_03_486245.csv'
SALES_BLOB = 'item_wise_sales_report/item_wise_sales_report_20-08-2026_03_00_01_867102.csv'

print("=== Peek at invoice CSV columns and first row ===")
rows = ch.query(f"""
    SELECT *
    FROM azureBlobStorage('{CONN}', 'sales-reports', '{INV_BLOB}', 'CSVWithNames')
    LIMIT 2
""").result_rows
print("Columns:", ch.query(f"""
    DESCRIBE (SELECT * FROM azureBlobStorage('{CONN}', 'sales-reports', '{INV_BLOB}', 'CSVWithNames') LIMIT 0)
""").result_rows)
print("Rows:")
for r in rows:
    print(r)

print("\n=== Peek at sales CSV ===")
rows = ch.query(f"""
    SELECT *
    FROM azureBlobStorage('{CONN}', 'sales-reports', '{SALES_BLOB}', 'CSVWithNames')
    LIMIT 2
""").result_rows
print("Columns:", ch.query(f"""
    DESCRIBE (SELECT * FROM azureBlobStorage('{CONN}', 'sales-reports', '{SALES_BLOB}', 'CSVWithNames') LIMIT 0)
""").result_rows)
print("Rows:")
for r in rows:
    print(r)
