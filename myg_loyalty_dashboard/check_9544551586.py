import duckdb

# Database path
DUCKDB_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'

# Mobile expression to harmonize formats
mobile_expr = 'CAST(TRY_CAST("Customer Mobile" AS DOUBLE) AS BIGINT)'

print(f"--- Database Check for Customer: 9544551586 ---")

try:
    conn = duckdb.connect(DUCKDB_PATH, read_only=True)
    
    # Query to see visit dates and invoice counts
    query = f'''
        SELECT 
            CAST(TRY_CAST("Date" AS VARCHAR) AS DATE) as visit_date,
            COUNT("Invoice Number") as total_invoices
        FROM sales_data 
        WHERE {mobile_expr} = 9544551586
        GROUP BY 1
        ORDER BY 1
    '''
    
    rows = conn.execute(query).fetchall()
    
    if not rows:
        print("Customer not found in the database.")
    else:
        for r in rows:
            print(f"Date: {r[0]} | Invoices: {r[1]}")
        print(f"\nTotal Distinct Visits: {len(rows)}")

except Exception as e:
    print(f"Error checking database: {e}")
