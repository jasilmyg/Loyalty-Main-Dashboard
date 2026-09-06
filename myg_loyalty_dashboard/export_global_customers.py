import os
import sys
import pandas as pd
import numpy as np
import time

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
import django
django.setup()
from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

output_dir = 'Exported_Global_Customers_Filtered'
os.makedirs(output_dir, exist_ok=True)

block_list_file = 'Block List updated (1).xlsx'
blocklist = set()
try:
    df_block = pd.read_excel(block_list_file, header=None)
    for val in df_block[0].dropna():
        cleaned = str(val).split('.')[0].strip()
        if len(cleaned) >= 10:
            blocklist.add(cleaned)
    print(f"Loaded {len(blocklist)} blocked numbers for filtering.")
except Exception as e:
    print(f"Error loading blocklist: {e}")
    sys.exit(1)

cutoff_date = '2023-09-01'

print("1. Fetching all ~4 million global active customers from ClickHouse...")
start_time = time.time()
query = f"""
    SELECT DISTINCT customer_mobile
    FROM azure_invoice_report
    WHERE date >= '{cutoff_date}'
      AND length(customer_mobile) >= 10
"""
res = client.query(query)
all_mobiles = [str(r[0]) for r in res.result_rows]

# Filter out block list
mobiles = [m for m in all_mobiles if m not in blocklist]
print(f" -> Removed {len(all_mobiles) - len(mobiles)} blocked numbers.")
print(f" -> Fetched {len(mobiles)} customers from database in {time.time() - start_time:.2f} seconds.")

print("\\n2. Splitting into 5 equal chunks...")
# Split the list into 5 chunks using numpy
chunks = np.array_split(mobiles, 5)

for i, chunk in enumerate(chunks, 1):
    print(f"  -> Writing Part {i} with {len(chunk)} numbers...")
    df = pd.DataFrame(chunk, columns=['Mobile Number'])
    file_path = os.path.join(output_dir, f'Global_Active_Customers_Part_{i}.xlsx')
    
    # Write to excel
    df.to_excel(file_path, index=False)
    print(f"     [OK] Saved {file_path}")

print("\\nSUCCESS: All 5 Excel files generated successfully!")
