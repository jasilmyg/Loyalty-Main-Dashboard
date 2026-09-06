import os
import sys
import pandas as pd
from datetime import datetime, timedelta

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'

import django
django.setup()

from analytics.clickhouse_service import get_ch_client
client = get_ch_client()

# 1. Load the blocklist
blocklist = set()
try:
    df_block = pd.read_excel('Block List updated.xlsx', header=None)
    for val in df_block[0].dropna():
        cleaned = str(val).split('.')[0].strip()
        if len(cleaned) >= 10:
            blocklist.add(cleaned)
    print(f"Loaded {len(blocklist)} numbers to blocklist.")
except Exception as e:
    print(f"Error loading blocklist: {e}")

# 2. Define branch mapping
groups = {
    'Panavila Future': ['PVF', 'TPTM', 'TPVD'],
    'Perinthalmanna Future': ['PMF', 'PMN'],
    'Valancherry future': ['VLC', 'VAL'],
    'Pattambi future': ['PIF', 'PTB'],
    'Kottayam Future': ['KAF', 'NGPM'],
    'Cherthala Future': ['CTF', 'CHL'],
    'Kottakkal future': ['KTF', 'KOT'],
    'Nilambur future': ['NBF', 'NIL'],
    'Mannarkkad future': ['MKF', 'MKD'],
    'Adoor Future': ['ADF', 'ADR'],
    'Thiruvalla Future': ['TVF', 'TRV'],
    'Kalpetta Future': ['KEF', 'KPT'],
    'Manthavady Future': ['MVF', 'MDY'],
    'Balussery Future': ['BLF', 'BLS'],
    'Thondayad': ['TDF', 'POT'],
    'Muvattupuzha Future': ['MTF', 'MUV'],
    'Karamana myG': ['KRMN'], 
    'Akkulam Future': ['AKF'], 
    'Kollam Future': ['KLF'], 
    'Kondotty future': ['KYF'], 
    'Palakkad Future': ['PKF'], 
    'Kanjirappally Future': ['KJF'], 
    'Kayamkulam myG': ['KYKM'], 
    'Pathanamthitta Future': ['PTF'], 
    'Bathery Future': ['SBF'], 
    'Nadakkavu': ['NKF'], 
    'Marindrive EPIC': ['MDF'], 
    'Edapally Future': ['EDY'], 
    'Poothole Future': ['POLE'], 
    'Vypin Future': ['VPF'], 
    'Kannur Future': ['KNF'], 
    'Thana myG': ['TNA'], 
    'SN Park myG': ['KNS'], 
    'Bank Road myG': ['KNB'],
    'Mavoor road': ['PUT', 'SEE', 'MAH', 'PCT', 'LAN', 'SBM', 'KIN', 'CAF', 'CAM']
}

code_to_group = {}
for group_name, codes in groups.items():
    for code in codes:
        code_to_group[code] = group_name

all_codes_str = "','".join(code_to_group.keys())

cutoff_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
print(f"Executing strict uniqueness query... Cutoff: {cutoff_date}")

query = f"""
    SELECT
        customer_mobile,
        argMax(branch, date) as primary_branch_code
    FROM azure_invoice_report
    WHERE branch IN ('{all_codes_str}')
      AND length(customer_mobile) >= 10
    GROUP BY customer_mobile
    HAVING max(date) >= '{cutoff_date}'
"""

res = client.query(query)
df = pd.DataFrame(res.result_rows, columns=['customer_mobile', 'primary_branch_code'])
print(f"Total globally unique customers retrieved: {len(df)}")

# 3. Filter blocklist
initial_len = len(df)
df = df[~df['customer_mobile'].isin(blocklist)]
print(f"Removed {initial_len - len(df)} customers from blocklist.")

# 4. Map to group
df['Group'] = df['primary_branch_code'].map(code_to_group)

# 5. Output to separate files
output_dir = 'Exported_Branches'
os.makedirs(output_dir, exist_ok=True)

for group_name in groups.keys():
    group_df = df[df['Group'] == group_name]
    final_df = group_df[['customer_mobile']].rename(columns={'customer_mobile': 'Mobile Number'})
    
    safe_name = group_name.replace('/', '_').replace('\\', '_')
    file_path = os.path.join(output_dir, f"{safe_name}.xlsx")
    
    final_df.to_excel(file_path, index=False)
    print(f"-> Wrote {len(final_df)} customers to {file_path}")

print("\\nSuccess! All branches extracted cleanly.")
