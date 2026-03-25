import duckdb

conn = duckdb.connect(r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb', read_only=True)

print('\n--- Raw rows for 9544551586 ---')
# Check any mobile numbers that contain '9544551586'
rows = conn.execute(
    'SELECT "Date", "Invoice Number", "Customer Name", "Customer Mobile" '
    'FROM sales_data WHERE "Customer Mobile" LIKE \'%9544551586%\''
).fetchall()

for r in rows:
    print(r)
    
print('\nTotal rows matching:', len(rows))
