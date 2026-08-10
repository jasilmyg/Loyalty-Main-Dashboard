"""Get v_sales_data view definition from local PostgreSQL."""
import os, sys, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    # Get view definition
    cursor.execute("SELECT pg_get_viewdef('v_sales_data', true)")
    viewdef = cursor.fetchone()
    print("=== v_sales_data VIEW DEFINITION ===")
    print(viewdef[0] if viewdef else "VIEW NOT FOUND")
    
    # Also check what columns it has
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'v_sales_data'
        ORDER BY ordinal_position
    """)
    cols = cursor.fetchall()
    print("\n=== COLUMNS ===")
    for c in cols:
        print(f"  {c[0]}: {c[1]}")
    
    # Check if sales_data table exists in PG
    cursor.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name IN ('sales_data', 'v_sales_data')
    """)
    tables = cursor.fetchall()
    print("\n=== EXISTING TABLES/VIEWS ===")
    for t in tables:
        print(f"  {t[0]}")
    
    # Count rows in sales_data if it exists
    try:
        cursor.execute("SELECT COUNT(*) FROM sales_data")
        count = cursor.fetchone()
        print(f"\nsales_data row count: {count[0]:,}")
    except Exception as e:
        print(f"\nsales_data count error: {e}")
