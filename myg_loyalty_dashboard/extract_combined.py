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

cutoff_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
print(f"Filtering out customers whose last visit is before {cutoff_date}")

excel_filename = 'Branch_Unique_Customers_v2.xlsx'

with pd.ExcelWriter(excel_filename, engine='xlsxwriter') as writer:
    # 1. Process individual branches from previous run
    for branch_name_excel, branch_code in branch_mapping.items():
        print(f"Processing {branch_name_excel} ({branch_code})...")
        query = f"""
            SELECT
                customer_mobile as `Mobile Number`,
                max(date) as `Last Visit Date`,
                count(DISTINCT invoice_no) as `Total Visits`,
                sum(invoice_total) as `Total Spent`
            FROM azure_invoice_report
            WHERE branch = '{branch_code}'
              AND length(customer_mobile) >= 10
            GROUP BY customer_mobile
            HAVING max(date) >= '{cutoff_date}'
        """
        try:
            res = client.query(query)
            df = pd.DataFrame(res.result_rows, columns=res.column_names)
            sheet_name = branch_name_excel[:31].replace(':', '').replace('/', '').replace('\\', '').replace('?', '').replace('*', '').replace('[', '').replace(']', '')
            df.to_excel(writer, sheet_name=sheet_name, index=False)
        except Exception as e:
            print(f"  -> ERROR processing {branch_name_excel}: {e}")

    # 2. Process Mavoor road (combined)
    print("Processing Mavoor road (combined)...")
    codes = "','".join(mavoor_road_branches.values())
    query = f"""
        SELECT
            customer_mobile as `Mobile Number`,
            max(date) as `Last Visit Date`,
            count(DISTINCT invoice_no) as `Total Visits`,
            sum(invoice_total) as `Total Spent`
        FROM azure_invoice_report
        WHERE branch IN ('{codes}')
          AND length(customer_mobile) >= 10
        GROUP BY customer_mobile
        HAVING max(date) >= '{cutoff_date}'
    """
    try:
        res = client.query(query)
        df = pd.DataFrame(res.result_rows, columns=res.column_names)
        df.to_excel(writer, sheet_name='Mavoor road', index=False)
        print(f"  -> Extracted {len(df)} unique customers for Mavoor road.")
    except Exception as e:
        print(f"  -> ERROR processing Mavoor road: {e}")

print(f"\\nSuccessfully generated {excel_filename}")
