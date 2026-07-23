import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

def setup_uid_column():
    print("Setting up Uid column and sequence...")
    with connection.cursor() as cur:
        # 1. Add the column (this is instant and does NOT lock the table)
        cur.execute('ALTER TABLE sales_data ADD COLUMN IF NOT EXISTS "Uid" BIGINT;')
        
        # 2. Create the sequence
        cur.execute('CREATE SEQUENCE IF NOT EXISTS sales_data_uid_seq;')
    print("Column and sequence created!")

def chunked_update():
    chunk_size = 50000
    total_updated = 0
    
    print(f"Starting background update in chunks of {chunk_size}...")
    
    while True:
        with connection.cursor() as cur:
            # We use ctid to safely grab the next 50k rows that don't have a Uid yet.
            # This avoids locking the whole table and works without an existing Primary Key.
            update_sql = f"""
                WITH cte AS (
                    SELECT ctid FROM sales_data WHERE "Uid" IS NULL LIMIT {chunk_size}
                )
                UPDATE sales_data 
                SET "Uid" = nextval('sales_data_uid_seq')
                WHERE ctid IN (SELECT ctid FROM cte);
            """
            cur.execute(update_sql)
            rows_affected = cur.rowcount
            
        if rows_affected == 0:
            print("All rows have been updated with a Uid!")
            break
            
        total_updated += rows_affected
        print(f"Updated {total_updated:,} rows so far... sleeping to let DB breathe")
        time.sleep(2)  # Sleep to prevent disk I/O from maxing out

if __name__ == "__main__":
    setup_uid_column()
    chunked_update()
    print("Done! You can now safely make 'Uid' a primary key.")
