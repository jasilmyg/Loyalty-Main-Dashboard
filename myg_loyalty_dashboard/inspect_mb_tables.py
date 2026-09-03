import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

# List all tables first
all_tables = ch.query("SELECT name FROM system.tables WHERE database=currentDatabase() ORDER BY name").result_rows
print("ALL TABLES:", [r[0] for r in all_tables])

tables = ['azure_sales_report', 'azure_invoice_report', 'branch_master', 'item_master']
for t in tables:
    try:
        rows = ch.query(f'DESCRIBE TABLE {t}').result_rows
        print(f'\n=== {t} ===')
        for r in rows:
            print(f'  {r[0]:40s}  {r[1]}')
        cnt = ch.query(f'SELECT count() FROM {t}').result_rows[0][0]
        print(f'  ROWS: {cnt:,}')
        # Show sample
        sample = ch.query(f'SELECT * FROM {t} LIMIT 2').result_rows
        print(f'  SAMPLE: {sample}')
    except Exception as e:
        print(f'  ERROR on {t}: {e}')
