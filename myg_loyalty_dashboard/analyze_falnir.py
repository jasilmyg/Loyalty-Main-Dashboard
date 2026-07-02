import os, sys, django
import pandas as pd

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

dates = ['2026-06-27', '2026-06-28']
store = 'Falnir Future'

total_new = 0
total_repeat = 0
total_cust = 0

with connection.cursor() as cur:
    for date_str in dates:
        # Construct possible string formats for the date
        d, m, y = date_str.split('-')[2], date_str.split('-')[1], date_str.split('-')[0]
        f1 = f"{d}-{m}-{y}%"
        f2 = f"{y}-{m}-{d}%"
        f3 = f"{d}/{m}/{y}%"
        
        cur.execute("""
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE "Branch" ILIKE %s
              AND ("Date" LIKE %s OR "Date" LIKE %s OR "Date" LIKE %s)
              AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != '';
        """, [f'%{store}%', f1, f2, f3])
        
        day_customers = [r[0] for r in cur.fetchall()]
        
        day_new = 0
        day_repeat = 0
        
        if day_customers:
            placeholders = ','.join(['%s'] * len(day_customers))
            cur.execute(f"""
                SELECT "Customer Mobile", "Date"
                FROM sales_data
                WHERE "Customer Mobile" IN ({placeholders})
            """, day_customers)
            
            rows = cur.fetchall()
            df = pd.DataFrame(rows, columns=['Customer Mobile', 'Date'])
            df['ParsedDate'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
            
            target_date = pd.to_datetime(date_str)
            
            for cust in day_customers:
                cust_df = df[df['Customer Mobile'] == cust]
                prior = cust_df[cust_df['ParsedDate'] < target_date]
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
