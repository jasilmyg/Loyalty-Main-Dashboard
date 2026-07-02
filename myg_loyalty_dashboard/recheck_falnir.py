import os, sys, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

file_path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\DSR JUNE 2026 PART Y.xlsx"
df = pd.read_excel(file_path, engine='calamine')

if 'Branch' in df.columns:
    df_falnir = df[df['Branch'].astype(str).str.contains('Falnir', case=False, na=False)].copy()
if 'Date' in df.columns:
    df['Customer Mobile'] = df['Customer Mobile'].astype(str).str.strip().str.replace('\.0$', '', regex=True)
    df_falnir['ParsedDate'] = pd.to_datetime(df_falnir['Date'], dayfirst=True, errors='coerce')
    df_falnir = df_falnir[df_falnir['ParsedDate'].dt.date.astype(str).isin(['2026-06-27', '2026-06-28'])]

total_new = 0
total_repeat = 0
total_cust = 0

with connection.cursor() as cur:
    for date_str in ['2026-06-27', '2026-06-28']:
        day_df = df_falnir[df_falnir['ParsedDate'].dt.date.astype(str) == date_str]
        day_customers = day_df['Customer Mobile'].dropna().astype(str).str.strip().str.replace('\.0$', '', regex=True).unique().tolist()
        
        day_new = 0
        day_repeat = 0
        
        if day_customers:
            placeholders = ','.join(['%s'] * len(day_customers))
            # Get the minimum date directly from the DB without date parsing errors by using a robust conversion
            cur.execute(f"""
                SELECT "Customer Mobile", "Date"
                FROM sales_data
                WHERE "Customer Mobile" IN ({placeholders})
            """, day_customers)
            rows = cur.fetchall()
            
            # Let's inspect the raw dates returned
            raw_dates = [r[1] for r in rows]
            # parse manually to be absolutely sure
            parsed = pd.to_datetime(raw_dates, dayfirst=True, errors='coerce')
            
            target_date = pd.to_datetime(date_str)
            
            df_db = pd.DataFrame({'Mobile': [r[0] for r in rows], 'ParsedDate': parsed})
            
            for cust in day_customers:
                cust_prior = df_db[(df_db['Mobile'] == cust) & (df_db['ParsedDate'] < target_date)]
                
                # We ALSO need to check if they had a prior purchase IN THE EXCEL FILE ITSELF !
                # E.g. if target date is 28th, did they purchase on 27th?
                # Or did they purchase earlier in the month in the same excel?
                excel_prior = df[(df['Customer Mobile'] == cust) & (pd.to_datetime(df['Date'], dayfirst=True, errors='coerce') < target_date)]
                
                if len(cust_prior) > 0 or len(excel_prior) > 0:
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
