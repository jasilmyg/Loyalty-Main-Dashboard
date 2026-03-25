import duckdb, os

DB_PATH = r'C:/Users/jasil_myg/Desktop/myG Loyalty Main Dashboard/project_folder/combined_data.db'
DUCKDB_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'

print('Backing up old duckdb...')
if os.path.exists(DUCKDB_PATH):
    # just remove it to create fresh
    try:
        os.remove(DUCKDB_PATH)
    except Exception as e:
        print("Could not remove old duckdb:", e)
        # Try finding the process holding it
        import psutil
        for proc in psutil.process_iter():
            try:
                for item in proc.open_files():
                    if DUCKDB_PATH in item.path:
                        print(f"File is open by: {proc.name()} (PID: {proc.pid})")
            except Exception:
                pass


print('Creating fresh DuckDB from SQLite with explicit casting...')
try:
    conn = duckdb.connect(DUCKDB_PATH)
    conn.execute(f"ATTACH '{DB_PATH}' AS sqlite_db (TYPE SQLITE);")
    
    # Cast Total Value and Date during import
    # Note: Using TRY_CAST/TRY_STRPTIME to be safe against bad data
    conn.execute(f"""
        CREATE TABLE sales_data AS 
        SELECT 
            *,
            TRY_CAST(REPLACE(REPLACE("Total Value", ',', ''), ' ', '') AS DOUBLE) as total_value_new,
            COALESCE(
                TRY_STRPTIME("Date", '%d-%m-%Y'), 
                TRY_CAST("Date" AS TIMESTAMP)
            ) as date_new
        FROM sqlite_db.sales_data;
    """)
    
    # Replace old columns with new typed ones
    conn.execute('ALTER TABLE sales_data DROP "Total Value";')
    conn.execute('ALTER TABLE sales_data RENAME total_value_new TO "Total Value";')
    conn.execute('ALTER TABLE sales_data DROP "Date";')
    conn.execute('ALTER TABLE sales_data RENAME date_new TO "Date";')
    
    print('Done! Rows created:', conn.execute('SELECT COUNT(*) FROM sales_data').fetchone()[0])
    print('Schema updated for analytics.')
except Exception as e:
    print('Error creating DuckDB:', e)
