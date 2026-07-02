import psycopg2
import pandas as pd
import numpy as np
import datetime
import os
import sys

# Connect to database using standard credentials for this project
print("Connecting to DB...")
conn = psycopg2.connect(
    host='db-postgresql-blr1-90397-do-user-3146770-0.e.db.ondigitalocean.com',
    port=25060,
    database='defaultdb',
    user='doadmin',
    password='HIDDEN_PASSWORD',
    sslmode='require'
)

print("Connected to DB.")
# 1. Fetch first purchase date for all customers from DB
print("Fetching DB data... This might take a few moments.")
cur = conn.cursor()
cur.execute('SELECT "Customer Mobile", MIN("Date") FROM sales_data WHERE "Customer Mobile" IS NOT NULL GROUP BY "Customer Mobile"')
rows = cur.fetchall()
cur.close()
conn.close()

print(f"Fetched {len(rows)} customers from DB. Converting to DataFrame...")
# Use pandas vectorization for speed
df_db = pd.DataFrame(rows, columns=['Customer Mobile', 'Date'])
df_db['Customer Mobile'] = df_db['Customer Mobile'].astype(str).str.strip()
# It's faster to convert strings to datetime in pandas than in a pure python loop
df_db['Date'] = pd.to_datetime(df_db['Date'], errors='coerce', dayfirst=True).dt.date
# Drop na dates
df_db = df_db.dropna(subset=['Date'])
# Create dictionary mapping mobile to first date
db_first_dates = dict(zip(df_db['Customer Mobile'], df_db['Date']))
print(f"Loaded {len(db_first_dates)} unique customers into memory mapping.")

# 2. Read Store mapping
print("Loading Store Mapping...")
df_store = pd.read_excel('STORE AND DISTRICT DATA.xlsx', engine='calamine')
# Map Store Name (upper, stripped) to District (upper, stripped)
store_to_district = {}
for _, row in df_store.iterrows():
    if pd.notna(row['Store Name']) and pd.notna(row['District']):
        store_name = str(row['Store Name']).strip().upper()
        district = str(row['District']).strip().upper()
        store_to_district[store_name] = district

# 3. Read DSR JUNE PART Z.xlsx
print("Loading DSR June Part Z...")
df_june = pd.read_excel('DSR JUNE PART Z.xlsx', engine='calamine')

# Filter SMC/EI in Invoice Number
df_june = df_june[~df_june['Invoice Number'].astype(str).str.contains('SMC/EI', case=False, na=False)]
print(f"Rows after filtering SMC/EI: {len(df_june)}")

# Standardize date
df_june['Date'] = pd.to_datetime(df_june['Date'], dayfirst=True, errors='coerce').dt.date
df_june['Customer Mobile'] = df_june['Customer Mobile'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
df_june['Branch'] = df_june['Branch'].astype(str).str.strip().str.upper()

# Assign District
df_june['District'] = df_june['Branch'].map(store_to_district)

# Filter out HEAD OFFICE and UG SMART CHOICE
df_june = df_june[~df_june['Branch'].isin(['HEAD OFFICE', 'UG SMART CHOICE'])]
print(f"Rows after branch filtering: {len(df_june)}")

# Sort by Date so we process chronologically
df_june = df_june.sort_values('Date')

results = []

# Process day by day
for dt, group in df_june.groupby('Date'):
    if not isinstance(dt, datetime.date):
        continue
    
    # Check for target districts
    for district in ['KOZHIKODE', 'THIRUVANANTHAPURAM']:
        dist_group = group[group['District'] == district]
        
        # Unique customers in this district today
        unique_customers = dist_group['Customer Mobile'].dropna().unique()
        
        new_count = 0
        repeat_count = 0
        
        for mobile in unique_customers:
            if mobile == 'nan' or mobile == '':
                continue
                
            first_db_date = db_first_dates.get(mobile, None)
            
            # Is Repeat if they have a purchase BEFORE today
            if first_db_date is not None and first_db_date < dt:
                repeat_count += 1
            else:
                # New customer
                new_count += 1
                
                # Update their first purchase date so they are counted as repeat tomorrow
                if mobile not in db_first_dates:
                    db_first_dates[mobile] = dt
        
        total = new_count + repeat_count
        new_pct = (new_count / total * 100) if total > 0 else 0
        rep_pct = (repeat_count / total * 100) if total > 0 else 0
        
        results.append({
            'Date': dt,
            'District': district,
            'Total': total,
            'New': new_count,
            'Repeat': repeat_count,
            'New %': f"{round(new_pct, 2)}%",
            'Repeat %': f"{round(rep_pct, 2)}%"
        })

print("\n" + "="*80)
print("--- FINAL RESULTS ---")
print("="*80)
results_df = pd.DataFrame(results)
print(results_df.to_string(index=False))
print("="*80)
