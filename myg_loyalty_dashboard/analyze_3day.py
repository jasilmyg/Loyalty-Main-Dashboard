import os, sys, django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

def analyze_3day_overall():
    target_dates = ['2026-06-24', '2026-06-25', '2026-06-26']
    base_date_limit = '2026-06-23'
    branch_name = 'Balussery Future'
    
    with connection.cursor() as cur:
        # Step 1: Get overall distinct customers for the 3 days
        cur.execute("""
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE parsed_date IN ('2026-06-24', '2026-06-25', '2026-06-26')
              AND "Branch" ILIKE %s
              AND "Customer Mobile" IS NOT NULL
              AND "Customer Mobile" != ''
        """, [f'%{branch_name}%'])
        
        customers = [row[0] for row in cur.fetchall()]
        total_customers = len(customers)
        
        # Step 2: Check repeat based on STRICT definition (Balussery Future only)
        cur.execute("""
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE parsed_date <= %s
              AND "Branch" ILIKE %s
              AND "Customer Mobile" = ANY(%s)
        """, [base_date_limit, f'%{branch_name}%', customers])
        
        repeat_mobiles = set(row[0] for row in cur.fetchall())
        repeat_count = len(repeat_mobiles)
        new_count = total_customers - repeat_count
        
        print("--- STRICT DEFINITION (Balussery Future prior only) ---")
        print(f"Total Unique: {total_customers}")
        print(f"Repeat: {repeat_count}")
        print(f"New: {new_count}")

        # Step 3: Check repeat based on LOOSE definition (Any branch prior)
        cur.execute("""
            SELECT DISTINCT "Customer Mobile"
            FROM sales_data
            WHERE parsed_date <= %s
              AND "Customer Mobile" = ANY(%s)
        """, [base_date_limit, customers])
        
        repeat_mobiles_loose = set(row[0] for row in cur.fetchall())
        repeat_count_loose = len(repeat_mobiles_loose)
        new_count_loose = total_customers - repeat_count_loose
        
        print("\\n--- LOOSE DEFINITION (Any branch prior) ---")
        print(f"Total Unique: {total_customers}")
        print(f"Repeat: {repeat_count_loose}")
        print(f"New: {new_count_loose}")

if __name__ == "__main__":
    analyze_3day_overall()
