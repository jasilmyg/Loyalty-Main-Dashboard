"""MY PARF comprehensive analysis from ClickHouse."""
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

# Step 1: Find PARF items
print('=== Step 1: Finding PARF items in item_master ===')
parf_items = ch.query("""
    SELECT item_code, product, brand, category, item_name, item_group, item_category, mrp, mop
    FROM item_master
    WHERE upper(category) LIKE '%PARF%'
       OR upper(item_category) LIKE '%PARF%'
       OR upper(product) LIKE '%PARF%'
       OR upper(item_name) LIKE '%PARF%'
       OR upper(brand) LIKE '%PARF%'
    LIMIT 30
""").result_rows
print(f'PARF items found: {len(parf_items)}')
for r in parf_items[:5]:
    print(f'  {r[0]} | {r[1][:30]} | brand={r[2]} | cat={r[3]} | item_cat={r[6]} | mrp={r[7]}')

# Step 2: Check azure_sales_report columns
print()
print('=== Step 2: azure_sales_report columns ===')
cols = ch.query('DESCRIBE TABLE azure_sales_report').result_rows
for c in cols:
    print(f'  {c[0]} : {c[1]}')

# Step 3: Find PARF in azure_sales_report
print()
print('=== Step 3: Checking MY PARF view in views.py ===')

# Check the MyParfDataAPIView
import subprocess
result = subprocess.run(['findstr', '/n', 'parf\|MY.PARF\|my_parf', 'dashboard\\views.py'], capture_output=True, text=True, cwd='.')
print(result.stdout[:3000])
