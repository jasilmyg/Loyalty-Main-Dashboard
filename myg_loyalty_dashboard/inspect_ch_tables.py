"""
Inspect all ClickHouse tables to understand the duplicate table issue.
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()
if not client:
    print('ERROR: Cannot connect to ClickHouse')
    exit(1)

print('=== ALL TABLES IN CLICKHOUSE ===\n')
rows = client.query('''
    SELECT 
        name, 
        engine,
        total_rows,
        formatReadableSize(total_bytes) as size,
        create_table_query
    FROM system.tables
    WHERE database = currentDatabase()
    ORDER BY name
''').result_rows

for r in rows:
    print(f'Table  : {r[0]}')
    print(f'Engine : {r[1]}')
    print(f'Rows   : {r[2]:,}' if r[2] else f'Rows   : 0')
    print(f'Size   : {r[3]}')
    print(f'DDL    : {r[4][:400]}')
    print('-' * 60)
