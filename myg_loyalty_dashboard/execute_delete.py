import os, sys, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client

client = get_ch_client()

combined_cond = "(invoice_number LIKE '%SMC%') OR (invoice_number LIKE '%EI%') OR (branch ILIKE '%HEAD OFFICE%') OR (branch ILIKE '%UG SMART CHOICE%')"

total_q = f"SELECT count() FROM sales_data WHERE {combined_cond}"
total = client.query(total_q).result_rows[0][0]
print(f"Total rows to delete: {total}")

if total > 0:
    print("Executing mutation...")
    client.command(f"ALTER TABLE sales_data DELETE WHERE {combined_cond}")
    
    # Wait for mutation to complete
    print("Waiting for mutation to complete (ClickHouse deletes are async)...")
    while True:
        # Check running mutations
        mutations = client.query("SELECT is_done FROM system.mutations WHERE table = 'sales_data' AND is_done = 0").result_rows
        if not mutations:
            break
        time.sleep(1)
        
    print("Deletion completed.")
    
    # Verify
    new_total = client.query(total_q).result_rows[0][0]
    print(f"Remaining rows matching conditions: {new_total}")
else:
    print("No rows to delete.")
