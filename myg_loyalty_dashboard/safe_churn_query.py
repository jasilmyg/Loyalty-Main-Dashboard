import os
import sys
import django

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

# Step 1: Get high spenders in Q2 2025
sql_q2 = """
    SELECT "Customer Mobile", SUM("Total Value")
    FROM sales_data
    WHERE parsed_date >= '2025-04-01' AND parsed_date <= '2025-06-30'
    AND "Customer Mobile" IS NOT NULL AND "Customer Mobile" != ''
    GROUP BY "Customer Mobile"
    HAVING SUM("Total Value") > 100000
"""

high_spenders = []
with connection.cursor() as cursor:
    print("Fetching Q2 2025 high spenders...")
    cursor.execute(sql_q2)
    for row in cursor.fetchall():
        high_spenders.append(row[0])

print(f"Found {len(high_spenders)} high spenders in Q2 2025.")

# Step 2: Check in chunks if they purchased after 2025-06-30
lost_count = 0
chunk_size = 1000

for i in range(0, len(high_spenders), chunk_size):
    chunk = high_spenders[i:i+chunk_size]
    # format tuple for SQL IN clause
    if len(chunk) == 1:
        in_clause = f"('{chunk[0]}')"
    else:
        in_clause = str(tuple(chunk))
        
    sql_check = f"""
        SELECT DISTINCT "Customer Mobile"
        FROM sales_data
        WHERE parsed_date > '2025-06-30'
        AND "Customer Mobile" IN {in_clause}
    """
    with connection.cursor() as cursor:
        cursor.execute(sql_check)
        retained = {row[0] for row in cursor.fetchall()}
        
        # Lost customers in this chunk are those who are NOT in the retained set
        chunk_lost = set(chunk) - retained
        lost_count += len(chunk_lost)
    
    print(f"Processed {min(i+chunk_size, len(high_spenders))}/{len(high_spenders)}...")

print(f"\nFINAL ANSWER:")
print(f"Total High Spenders in Q2 2025: {len(high_spenders)}")
print(f"Lost Customers (Never returned after Q2 2025): {lost_count}")
