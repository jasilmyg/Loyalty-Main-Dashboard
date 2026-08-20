import os
import django
import sys
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

SAS_TOKEN = "sp=racwl&st=2026-08-11T03:51:43Z&se=2026-12-31T18:29:43Z&spr=https&sv=2026-02-06&sr=c&sig=b5URyZCBQKQU3rwuqxY5z2vqyKNrsDKIPABLQ%2FFyywQ%3D"
ACCOUNT_URL = "https://stmygoalposreports.blob.core.windows.net"
CONNECTION_STRING = f"BlobEndpoint={ACCOUNT_URL}/;SharedAccessSignature={SAS_TOKEN}"

INV_BLOB = 'invoice_wise_sales_report/invoice_wise_sales_report_20-08-2026_03_00_03_486245.csv'
SALES_BLOB = 'item_wise_sales_report/item_wise_sales_report_20-08-2026_03_00_01_867102.csv'

print('Loading Aug 19 data into azure_invoice_report...')
q_inv = f"""
INSERT INTO azure_invoice_report
SELECT * FROM azureBlobStorage(
    '{CONNECTION_STRING}', 
    'sales-reports', 
    '{INV_BLOB}', 
    'CSVWithNames'
)
WHERE branch NOT IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
  AND invoice_no NOT LIKE '%SMC%'
  AND invoice_no NOT LIKE '%EI%'
SETTINGS date_time_input_format = 'best_effort'
"""
try:
    client.command(q_inv)
    print("Successfully loaded invoice data.")
except Exception as e:
    print(f"Error loading invoice data: {e}")

print('Loading Aug 19 data into azure_sales_report...')
q_sales = f"""
INSERT INTO azure_sales_report
SELECT * FROM azureBlobStorage(
    '{CONNECTION_STRING}', 
    'sales-reports', 
    '{SALES_BLOB}', 
    'CSVWithNames'
)
WHERE branch NOT IN ('3GH', 'SMC', 'HEAD OFFICE', 'UG SMART CHOICE')
  AND invoice_no NOT LIKE '%SMC%'
  AND invoice_no NOT LIKE '%EI%'
SETTINGS date_time_input_format = 'best_effort'
"""
try:
    client.command(q_sales)
    print("Successfully loaded sales data.")
except Exception as e:
    print(f"Error loading sales data: {e}")
