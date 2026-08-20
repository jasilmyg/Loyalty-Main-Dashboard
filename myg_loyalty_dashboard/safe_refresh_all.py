import os, django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

def refresh_all_mvs():
    start_time = time.time()
    
    print("Fetching all materialized views dynamically...")
    with connection.cursor() as cur:
        cur.execute("SELECT matviewname FROM pg_matviews;")
        mviews = [row[0] for row in cur.fetchall()]
        
    print(f"Found {len(mviews)} Materialized Views. Starting sequential safe refresh...")
    
    success_count = 0
    fail_count = 0
    
    for i, view in enumerate(mviews, 1):
        # We create a fresh connection or ensure timeout is 0
        try:
            with connection.cursor() as cur:
                # Disable statement timeout for this heavy operation
                cur.execute("SET statement_timeout = 0;")
                
                print(f"[{i}/{len(mviews)}] Refreshing {view}...")
                
                # First try CONCURRENTLY (if it has a unique index)
                try:
                    cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{view}";')
                    print(f"  -> Success (CONCURRENT)")
                except Exception as e:
                    # Fallback to standard refresh
                    cur.execute(f'REFRESH MATERIALIZED VIEW "{view}";')
                    print(f"  -> Success (STANDARD)")
                    
                success_count += 1
        except Exception as e:
            print(f"  -> FAILED: {e}")
            fail_count += 1
            # Close connection if failed to prevent transaction abortion errors
            connection.close()

    from django.core.cache import cache
    cache.clear()
    
    elapsed = time.time() - start_time
    print(f"\nDone! Successfully refreshed {success_count}/{len(mviews)} Materialized Views.")
    print(f"Django cache cleared. Total time: {elapsed:.1f} seconds.")

if __name__ == "__main__":
    refresh_all_mvs()
