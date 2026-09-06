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

# Previous branches
branch_mapping = {
    'Karamana myG': 'KRMN', 
    'Panavila Future': 'PVF', 
    'Akkulam Future': 'AKF', 
    'Kollam Future': 'KLF', 
    'Perinthalmanna future': 'PMF', 
    'Valancherry future': 'VLC', 
    'Kottakkal future': 'KTF', 
    'Nilambur future': 'NBF', 
    'Mannarkkad future': 'MKF', 
    'Pattambi future': 'PIF', 
    'Kondotty future': 'KYF', 
    'Palakkad Future': 'PKF', 
    'Kottayam Future': 'KAF', 
    'Kanjirappally Future': 'KJF', 
    'Cherthala Future': 'CTF', 
    'Kayamkulam myG': 'KYKM', 
    'Adoor Future': 'ADF', 
    'Pathanamthitta Future': 'PTF', 
    'Thiruvalla Future': 'TVF', 
    'Bathery Future': 'SBF', 
    'Kalpetta Future': 'KEF', 
    'Manthavady Future': 'MVF', 
    'Balussery Future': 'BLF', 
    'Thondayad': 'TDF', 
    'Nadakkavu': 'NKF', 
    'Marindrive EPIC': 'MDF', 
    'Edapally Future': 'EDY', 
    'Muvattupuzha Future': 'MTF', 
    'Poothole Future': 'POLE', 
    'Vypin Future': 'VPF', 
    'Kannur Future': 'KNF', 
    'Thana myG': 'TNA', 
    'SN Park myG': 'KNS', 
    'Bank Road myG': 'KNB'
}

# New branches that go into "Mavoor road"
mavoor_road_branches = {
    'MyG Puthiyara': 'PUT',
    'MyG SEEMA': 'SEE',
    'MyG SABA MAHARANI': 'MAH',
    'MyG P TOWER': 'PCT',
    'MyG LANDSHIP MALL': 'LAN',
    'MyG SABA': 'SBM',
    'MyG KINGSWAY': 'KIN',
    'MyG YMCA CAFE': 'CAF',
    'MyG CAM WORLD': 'CAM'
}

# Create a reverse mapping to identify which sheet a code belongs to
code_to_sheet = {}
for sheet, code in branch_mapping.items():
    code_to_sheet[code] = sheet[:31].replace(':', '').replace('/', '').replace('\\', '').replace('?', '').replace('*', '').replace('[', '').replace(']', '')

for sheet, code in mavoor_road_branches.items():
    code_to_sheet[code] = 'Mavoor road'

all_codes = list(code_to_sheet.keys())
codes_str = "','".join(all_codes)

cutoff_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
print(f"Filtering out customers whose last visit is before {cutoff_date}")
print("Executing strict unique query...")

query = f"""
    SELECT
        customer_mobile as `Mobile Number`,
        argMax(branch, date) as primary_branch_code,
        max(date) as `Last Visit Date`,
        count(DISTINCT invoice_no) as `Total Visits`,
        sum(invoice_total) as `Total Spent`
    FROM azure_invoice_report
    WHERE branch IN ('{codes_str}')
      AND length(customer_mobile) >= 10
    GROUP BY customer_mobile
    HAVING max(date) >= '{cutoff_date}'
"""

res = client.query(query)
df = pd.DataFrame(res.result_rows, columns=res.column_names)

print(f"Total globally unique customers across all specified branches: {len(df)}")

# Map each customer to their primary sheet
df['Sheet'] = df['primary_branch_code'].map(code_to_sheet)

# Drop the internal branch code column since it's just for routing
df.drop(columns=['primary_branch_code'], inplace=True)

excel_filename = 'Branch_Strict_Unique_Customers.xlsx'
with pd.ExcelWriter(excel_filename, engine='xlsxwriter') as writer:
    # Get unique sheets
    sheets = df['Sheet'].unique()
    for sheet in sheets:
        if pd.isna(sheet):
            continue
        sheet_df = df[df['Sheet'] == sheet].drop(columns=['Sheet'])
        sheet_df.to_excel(writer, sheet_name=sheet, index=False)
        print(f"  -> Wrote {len(sheet_df)} customers to sheet '{sheet}'")
        
    # Make sure we also create empty sheets for branches that somehow had 0 customers 
    # (just in case they were completely overshadowed)
    all_target_sheets = set(code_to_sheet.values())
    for s in all_target_sheets:
        if s not in sheets:
            pd.DataFrame(columns=['Mobile Number', 'Last Visit Date', 'Total Visits', 'Total Spent']).to_excel(writer, sheet_name=s, index=False)
            print(f"  -> Wrote 0 customers to sheet '{s}' (all customers shopped elsewhere more recently)")

print(f"\nSuccessfully generated {excel_filename}")
