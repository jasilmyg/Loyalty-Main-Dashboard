import psycopg2
import pandas as pd
import numpy as np
import datetime
import os
import django

# Connect to database using standard credentials for this project
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
print("Fetching DB data...")
cur = conn.cursor()
cur.execute('SELECT "Customer Mobile", MIN("Date") FROM sales_data WHERE "Customer Mobile" IS NOT NULL GROUP BY "Customer Mobile"')
db_first_dates = {str(row[0]).strip(): pd.to_datetime(row[1]).date() for row in cur.fetchall() if row[1]}
cur.close()
conn.close()
print(f"Fetched {len(db_first_dates)} customers from DB.")

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
df_june['Branch'] = df_june['Branch'].astype(str).str.strip().upper()

# Assign District
df_june['District'] = df_june['Branch'].map(store_to_district)

# Filter out HEAD OFFICE and UG SMART CHOICE (from the previous instructions just in case)
df_june = df_june[~df_june['Branch'].isin(['HEAD OFFICE', 'UG SMART CHOICE'])]
print(f"Rows after branch filtering: {len(df_june)}")

# Sort by Date so we process chronologically
df_june = df_june.sort_values('Date')

results = []

# Process day by day to correctly handle customers who appear on multiple days
for dt, group in df_june.groupby('Date'):
    if not isinstance(dt, datetime.date):
        continue
    
    # "Count each customer only once per day"
    # To do this, we get unique customers per branch per day. 
    # But wait, unique customers for the whole district? 
    # Let's count each customer uniquely per District per Day.
    
    # Process customers
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
                # If they have no prior purchase before today, they are New
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
            'New %': round(new_pct, 2),
            'Repeat %': round(rep_pct, 2)
        })

print("\n--- RESULTS ---")
results_df = pd.DataFrame(results)
print(results_df.to_string(index=False))
