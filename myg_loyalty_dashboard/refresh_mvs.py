import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.conf import settings
import psycopg2

def list_and_refresh_mvs():
    try:
        db = settings.DATABASES['default']
        conn = psycopg2.connect(
            host=db['HOST'],
            port=db['PORT'],
            dbname=db['NAME'],
            user=db['USER'],
            password=db['PASSWORD'],
            sslmode='require'
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        # List all materialized views
        cur.execute("""
            SELECT matviewname 
            FROM pg_matviews 
            WHERE schemaname = 'public'
            ORDER BY matviewname;
        """)
        mvs = [r[0] for r in cur.fetchall()]
        
        print(f"Found {len(mvs)} materialized views:")
        for mv in mvs:
            print(f"  - {mv}")
        
        if not mvs:
            print("No materialized views found.")
            return
            
        print("\nRefreshing all materialized views...")
        for mv in mvs:
            try:
                print(f"  Refreshing {mv}...", end=" ", flush=True)
                cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}"')
                print("OK")
            except Exception as e:
                # Try without CONCURRENTLY if it fails (no unique index)
                try:
                    cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
                    print("OK (non-concurrent)")
                except Exception as e2:
                    print(f"FAILED: {e2}")
        
        print("\nAll materialized views refreshed successfully!")
        conn.close()
        
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == '__main__':
    list_and_refresh_mvs()
