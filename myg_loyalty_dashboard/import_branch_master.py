import os
import django
import pandas as pd
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

def import_branch_master():
    client = get_ch_client()
    
    print("Creating branch_master table in ClickHouse...")
    client.command("""
        CREATE TABLE IF NOT EXISTS branch_master (
            code String,
            branch_name String,
            rbm String,
            bdm String,
            address String,
            district String,
            pincode String,
            email String,
            gst_no String,
            store_type String,
            phone_no String,
            category String,
            mapped_warehouse String
        ) ENGINE = MergeTree()
        ORDER BY code
    """)

    print("Truncating branch_master (if exists)...")
    client.command("TRUNCATE TABLE branch_master")

    file_path = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\project_folder\Branch and Warehouse List as on 27-08-2026.xlsx'
    print(f"Reading Excel file: {file_path}")
    start = time.time()
    
    df = pd.read_excel(file_path)
    print(f"Loaded {len(df)} rows in {time.time()-start:.2f}s")
    
    # Fill NAs
    df = df.fillna({
        'Code': '', 'Branch Name': '', 'RBM': '', 'BDM': '', 'Address': '', 
        'District': '', 'Pincode': '', 'Email': '', 'GSTNo': '', 'Store Type': '', 
        'Phone No': '', 'Category': '', 'Mapped Warehouse': ''
    })

    insert_data = []
    
    print("Preparing data for insertion...")
    for idx, row in df.iterrows():
        insert_data.append([
            str(row['Code']).strip(),
            str(row['Branch Name']).strip(),
            str(row['RBM']).strip(),
            str(row['BDM']).strip(),
            str(row['Address']).strip(),
            str(row['District']).strip(),
            str(row['Pincode']).strip(),
            str(row['Email']).strip(),
            str(row['GSTNo']).strip(),
            str(row['Store Type']).strip(),
            str(row['Phone No']).strip(),
            str(row['Category']).strip(),
            str(row['Mapped Warehouse']).strip()
        ])

    print("Inserting into ClickHouse...")
    start_insert = time.time()
    
    client.insert('branch_master', insert_data, column_names=[
        'code', 'branch_name', 'rbm', 'bdm', 'address', 
        'district', 'pincode', 'email', 'gst_no', 'store_type', 
        'phone_no', 'category', 'mapped_warehouse'
    ])
    
    print(f"Successfully inserted {len(insert_data)} rows in {time.time()-start_insert:.2f}s")

if __name__ == '__main__':
    import_branch_master()
