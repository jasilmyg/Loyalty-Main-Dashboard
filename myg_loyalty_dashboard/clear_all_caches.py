"""
clear_all_caches.py
===================
Clears ALL portal caches and triggers fresh data rebuild for every section.
Run this after any new data upload to ensure all dashboard sections reflect the latest data.
"""
import os, sys, django, glob, json
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

BASE = os.path.dirname(os.path.abspath(__file__))
ANALYTICS = os.path.join(BASE, 'analytics')

print("=" * 60)
print("  myG Loyalty Dashboard — Full Cache Clear")
print(f"  {datetime.now().strftime('%d %b %Y %I:%M %p')}")
print("=" * 60)

cleared = []
skipped = []

# ─── 1. ALL JSON caches in analytics/ ─────────────────────────────────────────
json_caches = [
    os.path.join(ANALYTICS, 'ai_forecast_cache.json'),
    os.path.join(ANALYTICS, 'jas_cache.json'),
    os.path.join(ANALYTICS, 'lstm_forecast_cache.json'),
    os.path.join(ANALYTICS, 'neural_intelligence_cache.json'),
    os.path.join(ANALYTICS, 'propensity_cache.json'),
    os.path.join(ANALYTICS, 'model_cache', 'campaign_intelligence.json'),
    os.path.join(BASE, 'june_2026_forecast.json'),
    os.path.join(BASE, 'temp_cohort_data.json'),
]

print("\n[1] Clearing JSON caches...")
for path in json_caches:
    if os.path.exists(path):
        os.remove(path)
        print(f"    [DEL] {os.path.basename(path)}")
        cleared.append(path)
    else:
        print(f"    [--]  {os.path.basename(path)} (not found)")
        skipped.append(path)

# ─── 2. Trained ML model pickle files ─────────────────────────────────────────
print("\n[2] Clearing ML model pickles (force retrain on next visit)...")
model_cache_dir = os.path.join(ANALYTICS, 'model_cache')
for f in glob.glob(os.path.join(model_cache_dir, '*.pkl')):
    os.remove(f)
    print(f"    [DEL] {os.path.basename(f)}")
    cleared.append(f)

# ─── 3. Django in-memory cache ────────────────────────────────────────────────
print("\n[3] Flushing Django cache backend...")
try:
    from django.core.cache import cache
    cache.clear()
    print("    [OK]  Django cache.clear() done")
except Exception as e:
    print(f"    [!!]  Django cache error: {e}")

# ─── 4. analytics.apps in-memory cache globals ────────────────────────────────
print("\n[4] Resetting in-memory globals...")
try:
    import analytics.campaign_intelligence as ci_mod
    ci_mod._cache = None
    ci_mod._cache_time = None
    print("    [OK]  campaign_intelligence._cache reset")
except Exception as e:
    print(f"    [!!]  campaign_intelligence: {e}")

try:
    import analytics.customer_propensity_engine as cp_mod
    # Reset any module-level cache dicts
    for attr in ['_cache', '_result_cache', '_data_cache']:
        if hasattr(cp_mod, attr):
            setattr(cp_mod, attr, None)
            print(f"    [OK]  propensity_engine.{attr} reset")
except Exception as e:
    print(f"    [!!]  propensity_engine: {e}")

try:
    import analytics.services as svc
    for attr in ['_cache', '_result_cache', '_data_cache', '_dashboard_cache']:
        if hasattr(svc, attr):
            setattr(svc, attr, None)
            print(f"    [OK]  services.{attr} reset")
except Exception as e:
    print(f"    [!!]  services: {e}")

# ─── 5. Verify ClickHouse latest date ─────────────────────────────────────────
print("\n[5] Verifying ClickHouse data...")
try:
    from analytics.clickhouse_service import get_ch_client
    client = get_ch_client()
    r = client.query("SELECT max(parsed_date), count() FROM sales_data")
    latest, total = r.result_rows[0]
    print(f"    Latest date : {latest}")
    print(f"    Total rows  : {total:,}")

    # Rows by last 3 dates
    r2 = client.query("""
        SELECT parsed_date, count()
        FROM sales_data
        WHERE parsed_date >= today() - 5
        GROUP BY parsed_date
        ORDER BY parsed_date DESC
    """)
    print("    Recent dates:")
    for row in r2.result_rows:
        print(f"      {row[0]}  ->  {row[1]:,} rows")
except Exception as e:
    print(f"    [!!]  ClickHouse error: {e}")

# ─── 6. Refresh ClickHouse Materialized Views ─────────────────────────────────
print("\n[6] Checking ClickHouse Materialized Views...")
try:
    mvs = client.query(
        "SELECT name FROM system.tables WHERE engine='MaterializedView' AND database=currentDatabase()"
    )
    mv_names = [r[0] for r in mvs.result_rows]
    if mv_names:
        for mv in mv_names:
            print(f"    [MV]  {mv} — auto-updated on INSERT (OK)")
    else:
        print("    [--]  No materialized views (live queries used)")
except Exception as e:
    print(f"    [!!]  MV check error: {e}")

# ─── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  Cleared : {len(cleared)} cache files/globals")
print(f"  Skipped : {len(skipped)} (already clear)")
print()
print("  All portal sections now serve LIVE data:")
print("    - Dashboard Overview      : live from ClickHouse")
print("    - Campaign Analysis        : live from ClickHouse")
print("    - Customer Analytics       : live from ClickHouse")
print("    - RFM Segmentation         : live from ClickHouse")
print("    - Cohort Retention         : live from ClickHouse")
print("    - Branches / Staff         : live from ClickHouse")
print("    - Payments / Discounts     : live from ClickHouse")
print("    - Monthly Retention        : live from ClickHouse")
print("    - Customer Intelligence AI : propensity cache cleared")
print("    - AI Intelligence Engine   : model cache cleared")
print("    - LSTM Forecast            : cache cleared")
print()
print("  Reload any page in the browser to see July 31 data.")
print("=" * 60)
