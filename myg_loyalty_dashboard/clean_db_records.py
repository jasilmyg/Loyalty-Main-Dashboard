import os
import django
from sqlalchemy import create_engine, text

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.conf import settings

def clean_database():
    import psycopg2
    
    db = settings.DATABASES['default']
    conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=require"
    engine = create_engine(conn_str)

    # Note: Using double quotes because column names have spaces or exact casing
    delete_query = text("""
        DELETE FROM sales_data 
        WHERE "Invoice Number" ILIKE '%SMC/EI%'
           OR UPPER(TRIM("Branch")) IN ('HEAD OFFICE', 'UG SMART CHOICE');
    """)

    try:
        with engine.begin() as conn:
            result = conn.execute(delete_query)
            print(f"SUCCESS: Deleted {result.rowcount} invalid records from PostgreSQL 'sales_data'.")
    except Exception as e:
        print(f"Error during deletion: {e}")

if __name__ == '__main__':
    clean_database()
