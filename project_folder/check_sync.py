import sqlite3
import os
import duckdb

SQLITE_PATH = r'C:/Users/jasil_myg/Desktop/myG Loyalty Main Dashboard/project_folder/combined_data.db'
DUCKDB_PATH = r'C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics.duckdb'

print("--- Data Sync Progress ---")

if os.path.exists(SQLITE_PATH):
    try:
        conn = sqlite3.connect(SQLITE_PATH)
        cursor = conn.cursor()
        
        # Check total rows
        cursor.execute("SELECT COUNT(*) FROM sales_data")
        count = cursor.fetchone()[0]
        
        # Check files processed
        cursor.execute("SELECT DISTINCT source_file FROM sales_data")
        files = [f[0] for f in cursor.fetchall()]
        
        print(f"Current Status: Syncing SQLite...")
        print(f"Rows Imported:  {count:,}")
        print(f"Files Done:     {len(files)} / 89")
        
        if files:
            print("\nLatest 3 files loaded:")
            for f in files[-3:]:
                print(f" - {f}")
        
        conn.close()
    except Exception as e:
        print("Progress: Database table is being initialized...")
else:
    print("Error: SQLite file not found yet.")

print("\n--- Analytics Cache (DuckDB) ---")
if os.path.exists(DUCKDB_PATH):
    size = os.path.getsize(DUCKDB_PATH) / (1024*1024)
    modified = os.path.getmtime(DUCKDB_PATH)
    import datetime
    mod_time = datetime.datetime.fromtimestamp(modified).strftime('%Y-%m-%d %H:%M:%S')
    print(f"Status:   Waiting for full sync to finish...")
    print(f"Updated:  {mod_time}")
    print(f"Size:     {size:.2f} MB")
else:
    print("DuckDB file not found.")

print("\n(Note: Once 'Files Done' reaches 89, the DuckDB will automatically update and the dashboard will be ready.)")
