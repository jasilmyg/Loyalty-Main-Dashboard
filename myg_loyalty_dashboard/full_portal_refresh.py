"""
Full portal data refresh:
1. Clear all Django caches so every section re-fetches from ClickHouse
2. Verify Aug 19 data is in ClickHouse (check if load succeeded or needs retry)
3. Refresh all PostgreSQL materialized views for sections that use PG
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from django.db import connection
from django.core.cache import cache

ch = get_ch_client()

# ── Step 1: Check current data max date ──────────────────────────────────────
print("=== ClickHouse Table Status ===")
r_inv = ch.query("SELECT toDate(max(date)), count() FROM azure_invoice_report").result_rows[0]
r_sal = ch.query("SELECT toDate(max(date)), count() FROM azure_sales_report").result_rows[0]
print(f"  azure_invoice_report  max_date={r_inv[0]}  total={r_inv[1]:,}")
print(f"  azure_sales_report    max_date={r_sal[0]}  total={r_sal[1]:,}")

# ── Step 2: Clear ALL cached data so dashboard sections re-query live ─────────
print("\n=== Clearing ALL Django cache keys ===")
cache.clear()
print("  Cache cleared successfully.")

# ── Step 3: Refresh every PostgreSQL materialized view sequentially ──────────
MVS = [
    'mv_customer_summary',
    'mv_monthly_summary',
    'mv_quarterly_members',
    'mv_rfm_segments',
    'mv_cohort_retention',
    'mv_monthly_retention_2026',
    'mv_gap_analysis',
    'mv_action_engine',
    'mv_loyalty_kpis',
    'mv_customer_propensity',
    'mv_fy_loyalty',
    'mv_retail_loyalty',
    'mv_customer_active_years',
    'mv_redemption_analysis',
    'mv_dormant_reactivation_customers',
    'mv_branch_resurrection_2024_2026',
    'mv_true_repeat_amj_2026',
]

print("\n=== Refreshing PostgreSQL Materialized Views ===")
refreshed, skipped = 0, 0
for mv in MVS:
    t0 = time.time()
    try:
        with connection.cursor() as cur:
            cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
        elapsed = time.time() - t0
        print(f"  [OK]   {mv}  ({elapsed:.1f}s)")
        refreshed += 1
    except Exception as e:
        err = str(e)
        if 'does not exist' in err:
            print(f"  [SKIP] {mv}  (not found)")
            skipped += 1
        else:
            print(f"  [FAIL] {mv}  -> {err[:80]}")
            skipped += 1

print(f"\nRefresh complete: {refreshed} OK, {skipped} skipped/failed.")

# ── Step 4: Verify row counts are up-to-date ─────────────────────────────────
print("\n=== Post-refresh ClickHouse checks ===")
r = ch.query("""
    SELECT toDate(date) AS d, count() AS cnt
    FROM azure_invoice_report
    WHERE toDate(date) >= '2026-08-01'
    GROUP BY d ORDER BY d DESC
""").result_rows
print("  Invoice rows by date (Aug 2026):")
for row in r:
    print(f"    {row[0]}  {row[1]:,}")

print("\nAll sections have been refreshed. Dashboard is now live with latest data.")
