import os
import django
import pandas as pd
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

def import_master():
    client = get_ch_client()
    
    print("Creating item_master table in ClickHouse...")
    client.command("""
        CREATE TABLE IF NOT EXISTS item_master (
            item_code String,
            product String,
            brand String,
            category String,
            item_name String,
            item_group String,
            item_category String,
            hsn String,
            tax_percent Float32,
            mop Float32,
            mrp Float32
        ) ENGINE = MergeTree()
        ORDER BY item_code
    """)

    print("Truncating item_master (if exists)...")
    client.command("TRUNCATE TABLE item_master")

    file_path = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\project_folder\Item Master as on 2026-08-27.xlsx'
    print(f"Reading Excel file: {file_path}")
    start = time.time()
    
    df = pd.read_excel(file_path)
    print(f"Loaded {len(df)} rows in {time.time()-start:.2f}s")
    
    # Handle column name casing variations
    if 'Mop' in df.columns: df.rename(columns={'Mop': 'MOP'}, inplace=True)
    if 'Mrp' in df.columns: df.rename(columns={'Mrp': 'MRP'}, inplace=True)

    # Fill NAs
    df = df.fillna({
        'Product': '', 'Brand': '', 'Category': '', 'Item': '', 
        'Item Code': '', 'Item Group': '', 'Item Category': '', 
        'HSN': '', 'Tax %': 0, 'MOP': 0, 'MRP': 0
    })

    # Prepare batch data
    # Select columns in the exact order of ClickHouse schema
    # ClickHouse Schema: item_code, product, brand, category, item_name, item_group, item_category, hsn, tax_percent, mop, mrp
    
    insert_data = []
    
    print("Preparing data for insertion...")
    for idx, row in df.iterrows():
        insert_data.append([
            str(row['Item Code']),
            str(row['Product']),
            str(row['Brand']),
            str(row['Category']),
            str(row['Item']),
            str(row['Item Group']),
            str(row['Item Category']),
            str(row['HSN']),
            float(row['Tax %']),
            float(row['MOP']),
            float(row['MRP'])
        ])

    print("Inserting into ClickHouse...")
    start_insert = time.time()
    # Insert in batches of 50000
    batch_size = 50000
    total_inserted = 0
    
    for i in range(0, len(insert_data), batch_size):
        batch = insert_data[i:i+batch_size]
        client.insert('item_master', batch, column_names=[
            'item_code', 'product', 'brand', 'category', 'item_name', 
            'item_group', 'item_category', 'hsn', 'tax_percent', 'mop', 'mrp'
        ])
        total_inserted += len(batch)
        print(f"Inserted {total_inserted}/{len(insert_data)} rows...")

    print(f"Successfully inserted {total_inserted} rows in {time.time()-start_insert:.2f}s")

if __name__ == '__main__':
    import_master()
