import os, sys, django
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

def analyze_customers_fast():
    target_dates = ['2026-06-24', '2026-06-25', '2026-06-26']
    base_date_limit = '2026-06-23'
    branch_name = 'Balussery Future'
    
    with connection.cursor() as cur:
        for target_date in target_dates:
            # Step 1: Get all unique customers for the specific date and branch
            cur.execute("""
                SELECT DISTINCT "Customer Mobile"
                FROM sales_data
                WHERE parsed_date = %s
                  AND "Branch" ILIKE %s
                  AND "Customer Mobile" IS NOT NULL
                  AND "Customer Mobile" != ''
            """, [target_date, f'%{branch_name}%'])
            
            customers_on_day = [row[0] for row in cur.fetchall()]
            total_customers = len(customers_on_day)
            
            if total_customers == 0:
                print(f"\\nDate: {target_date} | Branch: {branch_name}")
                print("Total Unique Customers: 0")
                print("Repeat Customers: 0")
                print("New Customers: 0")
                continue
                
            # Step 2: Check which of these customers exist in the base period FOR THIS SPECIFIC BRANCH ONLY
            cur.execute("""
                SELECT DISTINCT "Customer Mobile"
                FROM sales_data
                WHERE parsed_date <= %s
                  AND "Branch" ILIKE %s
                  AND "Customer Mobile" = ANY(%s)
            """, [base_date_limit, f'%{branch_name}%', customers_on_day])
            
            repeat_mobiles = set(row[0] for row in cur.fetchall())
            repeat_count = len(repeat_mobiles)
            new_count = total_customers - repeat_count
            
            print(f"\\nDate: {target_date} | Branch: {branch_name}")
            print(f"Total Unique Customers: {total_customers}")
            print(f"Repeat Customers: {repeat_count}")
            print(f"New Customers: {new_count}")

if __name__ == "__main__":
    analyze_customers_fast()
