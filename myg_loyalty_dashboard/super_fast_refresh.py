import os, django
import concurrent.futures
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.db import connection

def refresh_view(view_name):
    from django.db import connection
    try:
        cur = connection.cursor()
        print(f"[{view_name}] Starting refresh...")
        # Try concurrent refresh first (requires unique index, avoids locking)
        try:
            cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{view_name}";')
            print(f"[{view_name}] Refresh complete (CONCURRENT).")
        except Exception:
            # Fallback to standard refresh
            cur.execute(f'REFRESH MATERIALIZED VIEW "{view_name}";')
            print(f"[{view_name}] Refresh complete (STANDARD).")
        return True
    except Exception as e:
        print(f"[{view_name}] Failed: {e}")
        return False

if __name__ == "__main__":
    start_time = time.time()
    
    print("Fetching all materialized views dynamically...")
    with connection.cursor() as cur:
        cur.execute("SELECT matviewname FROM pg_matviews;")
        mviews = [row[0] for row in cur.fetchall()]
        
    print(f"Found {len(mviews)} Materialized Views. Starting concurrent refresh...")
    
    # Run up to 10 refreshes in parallel to maximize CPU/IO on Postgres
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(refresh_view, view): view for view in mviews}
        for future in concurrent.futures.as_completed(futures):
            view = futures[future]
            try:
                future.result()
            except Exception as exc:
                print(f"{view} generated an exception: {exc}")

    from django.core.cache import cache
    cache.clear()
    
    elapsed = time.time() - start_time
    print(f"\nDjango cache cleared. All {len(mviews)} Materialized Views refreshed concurrently in {elapsed:.1f} seconds!")
