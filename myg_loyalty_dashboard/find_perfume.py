import os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# Search item_master for perfume / my purf
print('=== Searching item_master for My Purf perfume ===')
r = client.query("""
    SELECT item_code, item_name, brand, category, product
    FROM item_master
    WHERE lower(item_name) LIKE '%perfume%'
       OR lower(item_name) LIKE '%purf%'
       OR lower(brand) LIKE '%purf%'
       OR lower(brand) LIKE '%perfume%'
       OR lower(product) LIKE '%purf%'
       OR lower(product) LIKE '%perfume%'
    LIMIT 50
""")
print(f'Found {len(r.result_rows)} items:')
for row in r.result_rows:
    print(f'  Code: {row[0]:15} Name: {row[1]:40} Brand: {row[2]:20} Cat: {row[3]}')

print()
# Also check distinct brands in item_master
print('=== All unique brands (sample) ===')
r2 = client.query("""
    SELECT DISTINCT brand, product
    FROM item_master
    WHERE lower(brand) LIKE '%my%'
       OR lower(product) LIKE '%my%'
    ORDER BY brand
    LIMIT 30
""")
for row in r2.result_rows:
    print(f'  Brand: {row[0]:30} Product: {row[1]}')

print()
# Also check categories
print('=== All categories in item_master ===')
r3 = client.query("""
    SELECT DISTINCT category, count() AS cnt
    FROM item_master
    GROUP BY category ORDER BY cnt DESC LIMIT 30
""")
for row in r3.result_rows:
    print(f'  {row[0]:30} {row[1]:,}')
