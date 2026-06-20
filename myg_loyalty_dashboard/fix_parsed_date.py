import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

cur = connection.cursor()

cur.execute("""
    SELECT tgname, pg_get_triggerdef(oid) 
    FROM pg_trigger 
    WHERE tgrelid = 'sales_data'::regclass;
""")
triggers = cur.fetchall()
print("Triggers on sales_data:")
for t in triggers:
    print(f"- {t[0]}: {t[1]}")

if not triggers:
    print("No triggers found. Let's fix parsed_date manually.")
    cur.execute(r"""
        UPDATE sales_data
        SET parsed_date = to_date("Date", 'DD-MM-YYYY')
        WHERE parsed_date IS NULL AND "Date" ~ '^\d{2}-\d{2}-\d{4}';
    """)
    updated = cur.rowcount
    connection.commit()
    print(f"Updated parsed_date for {updated} rows!")
