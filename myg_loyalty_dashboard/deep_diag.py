import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

cur = connection.cursor()

print("=" * 60)
print("DEEP DIAGNOSIS")
print("=" * 60)

# Check Date column format
cur.execute('SELECT "Date" FROM sales_data WHERE "Date" IS NOT NULL LIMIT 5;')
print("\nSample Date values:", cur.fetchall())

# Check parsed_date - how many rows have it populated
cur.execute('SELECT COUNT(*) FROM sales_data WHERE parsed_date IS NOT NULL;')
print(f"Rows with parsed_date: {cur.fetchone()[0]:,}")

cur.execute('SELECT COUNT(*) FROM sales_data WHERE parsed_date IS NULL;')
print(f"Rows WITHOUT parsed_date (NULL): {cur.fetchone()[0]:,}")

# Check total rows
cur.execute('SELECT COUNT(*) FROM sales_data;')
print(f"Total rows: {cur.fetchone()[0]:,}")

# Check latest Date strings  
cur.execute('SELECT "Date" FROM sales_data ORDER BY parsed_date DESC NULLS LAST LIMIT 5;')
print("\nLatest Date strings (by parsed_date):", cur.fetchall())

# Check how MV queries dates - look at mv_loyalty_kpis definition
cur.execute("""
    SELECT definition FROM pg_matviews WHERE matviewname = 'mv_loyalty_kpis';
""")
row = cur.fetchone()
if row:
    print("\nmv_loyalty_kpis definition (first 500 chars):")
    print(row[0][:500])
