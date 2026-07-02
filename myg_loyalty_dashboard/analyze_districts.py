import os, django
import pandas as pd
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

import warnings
warnings.filterwarnings("ignore")

# Read the excel mapping
df = pd.read_excel('STORE AND DISTRICT DATA.xlsx')
kozhikode_stores = df[df['District'].str.upper() == 'KOZHIKODE']['Store Name'].unique().tolist()
tvm_stores = df[df['District'].str.upper() == 'THIRUVANANTHAPURAM']['Store Name'].unique().tolist()

# Define queries
query = """
    WITH CustomerFirstPurchase AS (
        SELECT "Customer Mobile", MIN(parsed_date) as first_purchase_date
        FROM sales_data
        WHERE "Customer Mobile" IN (
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE parsed_date IN ('2026-06-29', '2026-06-30')
        )
        GROUP BY "Customer Mobile"
    ),
    TargetPurchases AS (
        SELECT 
            s.parsed_date, 
            s."Customer Mobile", 
            s."Branch",
            c.first_purchase_date
        FROM sales_data s
        JOIN CustomerFirstPurchase c ON s."Customer Mobile" = c."Customer Mobile"
        WHERE s.parsed_date IN ('2026-06-29', '2026-06-30')
    )
    SELECT * FROM TargetPurchases
"""
df_sales = pd.read_sql(query, connection)

# Add District column based on branch name matching
def get_district(branch):
    branch = str(branch).strip().upper()
    for s in kozhikode_stores:
        if s.strip().upper() in branch or branch in s.strip().upper():
            return 'KOZHIKODE'
    for s in tvm_stores:
        if s.strip().upper() in branch or branch in s.strip().upper():
            return 'THIRUVANANTHAPURAM'
    return 'OTHER'

df_sales['parsed_date'] = df_sales['parsed_date'].astype(str).str[:10]
df_sales['first_purchase_date'] = df_sales['first_purchase_date'].astype(str).str[:10]
df_sales['District'] = df_sales['Branch'].apply(get_district)
print("Total sales rows:", len(df_sales))
print("Unique branches in sales:", df_sales['Branch'].unique()[:5])
print("First 5 Kozhikode stores from Excel:", kozhikode_stores[:5])
print("Number of mapped KOZHIKODE rows:", len(df_sales[df_sales['District'] == 'KOZHIKODE']))

# Filter for the two districts
df_filtered = df_sales[df_sales['District'].isin(['KOZHIKODE', 'THIRUVANANTHAPURAM'])].copy()
df_filtered['Customer Mobile'] = df_filtered['Customer Mobile'].astype(str)

# Analyze Combined (29 and 30) for Districts and Stores
df_combined = df_filtered.drop_duplicates(subset=['Customer Mobile'])

# We need a function to analyze a dataframe and return metrics
def get_metrics(df_subset):
    total = len(df_subset)
    if total == 0:
        return {'new': 0, 'repeat': 0, 'total': 0, 'new_pct': 0.0, 'repeat_pct': 0.0}
    
    # A customer is 'New' for the combined period if their first purchase was on either 29th or 30th
    new_custs = len(df_subset[df_subset['first_purchase_date'].isin(['2026-06-29', '2026-06-30'])])
    repeat_custs = total - new_custs
    return {
        'new': new_custs,
        'repeat': repeat_custs,
        'total': total,
        'new_pct': round((new_custs / total) * 100, 2),
        'repeat_pct': round((repeat_custs / total) * 100, 2)
    }

print("COMBINED REPORT (29th & 30th June 2026)")
print("=" * 70)

for district in ['KOZHIKODE', 'THIRUVANANTHAPURAM']:
    dist_df = df_combined[df_combined['District'] == district]
    metrics = get_metrics(dist_df)
    
    print(f"\\n[{district} DISTRICT - OVERALL]")
    print(f"Total Unique Customers: {metrics['total']} | New: {metrics['new']} ({metrics['new_pct']}%) | Repeat: {metrics['repeat']} ({metrics['repeat_pct']}%)")
    print("-" * 50)
    print(f"Store-wise Breakdown ({district}):")
    
    # Store level breakdown
    stores = dist_df['Branch'].unique()
    store_results = []
    for store in stores:
        store_df = dist_df[dist_df['Branch'] == store]
        s_met = get_metrics(store_df)
        store_results.append({
            'store': store,
            'total': s_met['total'],
            'new': s_met['new'],
            'new_pct': s_met['new_pct'],
            'repeat': s_met['repeat'],
            'repeat_pct': s_met['repeat_pct']
        })
        
    # Sort by total descending
    store_results = sorted(store_results, key=lambda x: x['total'], reverse=True)
    for s in store_results:
        print(f"  {s['store'].ljust(25)}: Total={str(s['total']).ljust(4)} | New={str(s['new']).ljust(4)} ({str(s['new_pct']).rjust(5)}%) | Repeat={str(s['repeat']).ljust(4)} ({str(s['repeat_pct']).rjust(5)}%)")

