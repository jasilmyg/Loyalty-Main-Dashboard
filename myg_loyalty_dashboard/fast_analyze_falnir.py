import os, sys, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

# Read the excel file
file_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR JUNE 2026 PART Y.xlsx"
df = pd.read_excel(file_path, engine='calamine')

print(f"Loaded Excel: {df.shape}")

# Filter for Falnir Future
if 'Branch' in df.columns:
    df_falnir = df[df['Branch'].astype(str).str.contains('Falnir', case=False, na=False)].copy()
else:
    print("No Branch column")
    sys.exit()

if 'Date' in df.columns:
    df_falnir['ParsedDate'] = pd.to_datetime(df_falnir['Date'], dayfirst=True, errors='coerce')
    df_falnir = df_falnir[df_falnir['ParsedDate'].dt.date.astype(str).isin(['2026-06-27', '2026-06-28'])]
else:
    print("No Date column")
    sys.exit()

print(f"Falnir rows on 27/28: {df_falnir.shape[0]}")

total_new = 0
total_repeat = 0
total_cust = 0

with connection.cursor() as cur:
    for date_str in ['2026-06-27', '2026-06-28']:
        day_df = df_falnir[df_falnir['ParsedDate'].dt.date.astype(str) == date_str]
        
        # Unique customers
        day_customers = day_df['Customer Mobile'].dropna().unique().tolist()
        
        day_new = 0
        day_repeat = 0
        
        if day_customers:
            placeholders = ','.join(['%s'] * len(day_customers))
            # Check for prior purchases anywhere in the DB
            cur.execute(f"""
                SELECT "Customer Mobile", "Date"
                FROM sales_data
                WHERE "Customer Mobile" IN ({placeholders})
            """, day_customers)
            
            rows = cur.fetchall()
            db_df = pd.DataFrame(rows, columns=['Customer Mobile', 'Date'])
            db_df['ParsedDate'] = pd.to_datetime(db_df['Date'], dayfirst=True, errors='coerce')
            
            target_date = pd.to_datetime(date_str)
            
            for cust in day_customers:
                cust_db = db_df[db_df['Customer Mobile'] == cust]
                prior = cust_db[cust_db['ParsedDate'] < target_date]
                if len(prior) > 0:
                    day_repeat += 1
                else:
                    day_new += 1
                    
        total_new += day_new
        total_repeat += day_repeat
        total_cust += len(day_customers)
        print(f"Date {date_str}: Total={len(day_customers)} New={day_new} Repeat={day_repeat}")

print("="*40)
print(f"Total Customers: {total_cust} (100%)")
if total_cust > 0:
    print(f"Repeat Customer: {total_repeat} ({round((total_repeat/total_cust)*100)}%)")
    print(f"New Customer: {total_new} ({round((total_new/total_cust)*100)}%)")
else:
    print("Repeat Customer: 0 (0%)")
    print("New Customer: 0 (0%)")
print("="*40)
