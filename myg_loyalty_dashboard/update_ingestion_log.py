import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
from datetime import datetime

ch = get_ch_client()

# Check current state
total = ch.query('SELECT count() FROM azure_ingestion_log').result_rows[0][0]
latest = ch.query('SELECT file_name, ingested_at FROM azure_ingestion_log ORDER BY ingested_at DESC LIMIT 5').result_rows
print(f'Total log entries: {total}')
print('Latest 5 entries:')
for r in latest:
    print(f'  {r[0]}  |  {r[1]}')

# Check if Aug 15/16 already logged
aug_logged = ch.query(
    "SELECT file_name FROM azure_ingestion_log WHERE file_name LIKE '%15-08-2026%' OR file_name LIKE '%16-08-2026%'"
).result_rows

print()
if aug_logged:
    print('Already logged:')
    for r in aug_logged:
        print(f'  {r[0]}')
else:
    print('Aug 15 & 16 NOT yet in log — inserting now...')

    files_to_log = [
        'invoice_wise_sales_report/invoice_wise_sales_report_15-08-2026_03_00_03_422021.csv',
        'item_wise_sales_report/item_wise_sales_report_15-08-2026_03_00_02_127787.csv',
        'invoice_wise_sales_report/invoice_wise_sales_report_16-08-2026_03_00_03_853462.csv',
        'item_wise_sales_report/item_wise_sales_report_16-08-2026_03_00_02_005275.csv',
    ]

    now = datetime.now()
    rows = [[f, now] for f in files_to_log]
    ch.insert('azure_ingestion_log', rows, column_names=['file_name', 'ingested_at'])
    print(f'Inserted {len(rows)} entries into azure_ingestion_log')
    for f in files_to_log:
        print(f'  + {f}')

print()
# Final verify
new_total = ch.query('SELECT count() FROM azure_ingestion_log').result_rows[0][0]
print(f'azure_ingestion_log total entries: {new_total}')
print('Done!')
