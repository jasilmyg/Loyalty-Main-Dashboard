import duckdb
import os

DUCKDB_PATH = 'analytics.duckdb'

def check_metrics():
    if not os.path.exists(DUCKDB_PATH):
        print(f"File not found: {DUCKDB_PATH}")
        return
        
    try:
        conn = duckdb.connect(DUCKDB_PATH, read_only=True)
        
        # Check overall totals
        total_query = 'SELECT SUM("Total Value"), COUNT(*), COUNT(DISTINCT "Invoice Number") FROM sales_data'
        totals = conn.execute(total_query).fetchone()
        print(f"Overall Totals: {totals}")
        
        # Check column types again
        print("\nSchema:")
        print(conn.execute("DESCRIBE sales_data").fetchall()[-2:]) # Show Total Value and Date
        
        # Check first 5 rows of Total Value
        print("\nFirst 5 Values:")
        print(conn.execute('SELECT "Total Value", "Invoice Number" FROM sales_data LIMIT 5').fetchall())
        
        # Check monthly trend logic
        monthly_query = """
            SELECT 
                STRFTIME("Date", '%Y-%m') as month,
                SUM("Total Value") as revenue
            FROM sales_data
            GROUP BY month
            ORDER BY month ASC
            LIMIT 5
        """
        print("\nMonthly Trend Check:")
        print(conn.execute(monthly_query).fetchall())
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_metrics()
