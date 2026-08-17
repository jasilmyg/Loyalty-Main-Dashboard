import os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# Get revenue per product
r = client.query("""
    SELECT
        sr.item_code,
        im.item_name,
        round(sum(sr.qty), 0)       AS total_qty,
        round(sum(sr.sold_price), 0) AS total_revenue,
        round(avg(sr.sold_price), 0) AS avg_price
    FROM azure_sales_report sr
    JOIN item_master im ON sr.item_code = im.item_code
    WHERE im.brand = 'MY PARF'
    GROUP BY sr.item_code, im.item_name
    ORDER BY total_qty DESC
    LIMIT 15
""")

print('Top MY PARF Products:')
print(f'{"#":>3}  {"Item Code":15} {"Qty":>8} {"Revenue":>14} {"Avg Price":>10}  Name')
for i, row in enumerate(r.result_rows, 1):
    print(f'{i:>3}  {row[0]:15} {int(row[2]):>8,} {int(row[3]):>14,}  {int(row[4]):>10,}  {row[1][:50]}')
