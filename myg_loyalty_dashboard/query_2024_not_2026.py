import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection
cur = connection.cursor()

# Get EXACT column names for mv_branch_resurrection_2024_2026
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", ['mv_branch_resurrection_2024_2026'])
print("=== mv_branch_resurrection_2024_2026 columns ===")
for c in cur.fetchall():
    print(f"  {c}")

# Get EXACT column names for mv_dormant_reactivation
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", ['mv_dormant_reactivation'])
print("\n=== mv_dormant_reactivation columns ===")
for c in cur.fetchall():
    print(f"  {c}")

# Try the correct query using NOT EXISTS (faster than NOT IN on large datasets)
print("\n=== Running direct query on sales_data ===")
sql = """
    SELECT COUNT(DISTINCT sd."Customer Mobile") as unique_customers
    FROM sales_data sd
    WHERE EXTRACT(YEAR FROM sd.parsed_date) = 2024
      AND NOT EXISTS (
          SELECT 1
          FROM sales_data sd2
          WHERE sd2."Customer Mobile" = sd."Customer Mobile"
            AND EXTRACT(YEAR FROM sd2.parsed_date) = 2026
      )
"""
cur.execute(sql)
row = cur.fetchone()
print(f"Unique customers who purchased in 2024 but NOT in 2026: {row[0]:,}")
