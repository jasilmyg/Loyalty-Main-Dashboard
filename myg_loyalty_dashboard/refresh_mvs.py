import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from django.conf import settings
import psycopg2

def get_conn():
    db = settings.DATABASES['default']
    return psycopg2.connect(
        host=db['HOST'],
        port=db['PORT'],
        dbname=db['NAME'],
        user=db['USER'],
        password=db['PASSWORD'],
        sslmode='require'
    )

def list_and_refresh_mvs():
    try:
        # Step 1: Kill any existing refresh queries to avoid collisions
        print("Checking for existing refresh processes...")
        conn = get_conn()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT pid FROM pg_stat_activity
            WHERE query ILIKE '%REFRESH MATERIALIZED VIEW%'
            AND pid != pg_backend_pid();
        """)
        pids = [r[0] for r in cur.fetchall()]
        if pids:
            print(f"  Terminating {len(pids)} competing refresh process(es)...")
            for pid in pids:
                try:
                    cur.execute(f"SELECT pg_terminate_backend({pid});")
                except Exception:
                    pass
        else:
            print("  No competing processes found.")
        
        # Step 2: Get list of all materialized views
        cur.execute("""
            SELECT matviewname 
            FROM pg_matviews 
            WHERE schemaname = 'public'
            ORDER BY matviewname;
        """)
        mvs = [r[0] for r in cur.fetchall()]
        conn.close()
        
        print(f"\nFound {len(mvs)} materialized views:")
        for mv in mvs:
            print(f"  - {mv}")
        
        if not mvs:
            print("No materialized views found.")
            return
            
        # Step 3: Refresh each view with its own connection (resilient to SSL drops)
        print("\nRefreshing all materialized views...")
        for mv in mvs:
            try:
                print(f"  Refreshing {mv}...", end=" ", flush=True)
                conn = get_conn()
                conn.autocommit = True
                cur = conn.cursor()
                try:
                    cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv}"')
                    print("OK")
                except Exception:
                    try:
                        cur.execute(f'REFRESH MATERIALIZED VIEW "{mv}"')
                        print("OK (non-concurrent)")
                    except Exception as e2:
                        print(f"FAILED: {e2}")
                conn.close()
            except Exception as e:
                print(f"  Connection error on {mv}: {e}")
        
        print("\nAll materialized views refreshed successfully!")
        
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == '__main__':
    list_and_refresh_mvs()
