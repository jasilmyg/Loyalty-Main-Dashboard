"""
smart_tiered_refresh.py
========================
Refreshes all Materialized Views in 3 priority tiers:

  TIER 1 (Critical - dashboard home):  2 parallel workers
    → These run first so the dashboard shows data IMMEDIATELY.
    → mv_customer_summary, mv_monthly_summary, mv_loyalty_kpis,
      mv_monthly_members, mv_monthly_members_branch, mv_rfm_segments

  TIER 2 (Important - sub-pages):  3 parallel workers
    → mv_monthly_retention_2026, mv_dormant_reactivation,
      mv_dormant_reactivation_customers, mv_rfm_summary,
      mv_gap_analysis, mv_action_engine, mv_redemption_analysis,
      mv_branch_summary, mv_fy_sales, mv_fy_sales_branch,
      mv_fy_members, mv_fy_members_branch, mv_true_repeat_amj_2026

  TIER 3 (Heavy - cohorts/analytics - run sequentially):
    → mv_cohort_retention, mv_cohort_rfm, mv_cohort_cross_year,
      mv_cohort_customer_years, mv_customer_active_years,
      mv_customer_yearly_revenue, mv_customer_lifetime_summary,
      mv_customer_propensity, mv_yearly_cohort, mv_yearly_members,
      mv_yearly_members_branch, mv_quarterly_members,
      mv_quarterly_members_branch, mv_branch_resurrection_2024_2026,
      mv_yearly_customer_cohorts

Approach:
  - Before starting: kill any lingering REFRESH queries on Postgres
  - Each tier uses psycopg2 with autocommit=True (no transaction wrappers)
  - Small parallelism (2–3 workers max) to avoid lock contention
  - Cache is cleared after TIER 1 so the dashboard updates immediately
"""

import os, django, time, concurrent.futures
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

import psycopg2
from django.conf import settings
from django.core.cache import cache

def get_conn():
    db = settings.DATABASES['default']
    conn = psycopg2.connect(
        host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
        user=db['USER'], password=db['PASSWORD'], sslmode='require'
    )
    conn.autocommit = True
    return conn

def kill_existing_refreshes():
    """Terminate any stuck REFRESH MATERIALIZED VIEW queries."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT pg_cancel_backend(pid), pid
        FROM pg_stat_activity
        WHERE query ILIKE '%REFRESH MATERIALIZED VIEW%'
          AND state NOT IN ('idle')
          AND pid != pg_backend_pid();
    """)
    rows = cur.fetchall()
    if rows:
        print(f"  Killed {len(rows)} existing REFRESH queries: {[r[1] for r in rows]}")
    else:
        print("  No existing REFRESH queries found.")
    conn.close()

def refresh_mv(mv_name):
    """Refresh a single MV with its own connection. Returns (name, elapsed, status)."""
    t0 = time.time()
    try:
        conn = get_conn()
        cur  = conn.cursor()
        try:
            cur.execute(f'REFRESH MATERIALIZED VIEW CONCURRENTLY "{mv_name}";')
            status = "CONCURRENT"
        except psycopg2.errors.ObjectNotInPrerequisiteState:
            # No unique index - fall back to standard refresh
            conn.close()
            conn = get_conn()
            cur  = conn.cursor()
            cur.execute(f'REFRESH MATERIALIZED VIEW "{mv_name}";')
            status = "STANDARD"
        conn.close()
        elapsed = time.time() - t0
        return mv_name, elapsed, status, None
    except Exception as e:
        elapsed = time.time() - t0
        return mv_name, elapsed, "FAILED", str(e)

def run_tier(tier_name, mvs, max_workers):
    """Run a tier of MVs in parallel with limited concurrency."""
    print(f"\n{'='*60}")
    print(f"  {tier_name} --- {len(mvs)} MVs, {max_workers} parallel workers")
    print(f"{'='*60}")
    tier_start = time.time()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(refresh_mv, mv): mv for mv in mvs}
        for future in concurrent.futures.as_completed(futures):
            mv_name, elapsed, status, err = future.result()
            if err:
                print(f"  FAILED {mv_name:<45} in {elapsed:.1f}s: {err}", flush=True)
            else:
                print(f"  OK     {mv_name:<45} {status} in {elapsed:.1f}s", flush=True)
            results.append((mv_name, elapsed, status, err))
    print(f"  {tier_name} done in {time.time()-tier_start:.1f}s total.", flush=True)
    return results

# --- MV Tier Definitions ---------------------------------------------------
TIER1 = [
    # Dashboard home page + Customer Analytics (queried immediately on load)
    "mv_customer_summary",
    "mv_monthly_summary",
    "mv_loyalty_kpis",
    "mv_monthly_members",
    "mv_monthly_members_branch",
    "mv_rfm_segments",
]

TIER2 = [
    # Sub-pages: retention, campaign, redemption, branch, FY, staff
    "mv_monthly_retention_2026",
    "mv_dormant_reactivation",
    "mv_dormant_reactivation_customers",
    "mv_rfm_summary",
    "mv_gap_analysis",
    "mv_action_engine",
    "mv_redemption_analysis",
    "mv_branch_summary",
    "mv_fy_sales",
    "mv_fy_sales_branch",
    "mv_fy_members",
    "mv_fy_members_branch",
    "mv_true_repeat_amj_2026",
    "mv_branch_resurrection_2024_2026",
    "mv_quarterly_members",
    "mv_quarterly_members_branch",
    "mv_yearly_members",
    "mv_yearly_members_branch",
]

TIER3 = [
    # Heavy cohort & customer lifetime views — sequential to avoid locks
    "mv_yearly_cohort",
    "mv_cohort_retention",
    "mv_cohort_rfm",
    "mv_cohort_cross_year",
    "mv_cohort_customer_years",
    "mv_customer_active_years",
    "mv_customer_yearly_revenue",
    "mv_customer_lifetime_summary",
    "mv_customer_propensity",
    "mv_yearly_customer_cohorts",
]

if __name__ == "__main__":
    total_start = time.time()
    print("=" * 60)
    print("  SMART TIERED MV REFRESH")
    print("=" * 60)

    # Kill any lingering refreshes first
    print("\nStep 0: Killing any stuck REFRESH queries...")
    kill_existing_refreshes()

    # TIER 1 - Critical (2 parallel workers)
    run_tier("TIER 1 - Critical (Dashboard Home)", TIER1, max_workers=2)

    # Clear cache immediately so dashboard shows fresh data now
    cache.clear()
    print("\n  Cache cleared -- dashboard is now showing fresh TIER 1 data!\n")

    # TIER 2 - Important sub-pages (3 parallel workers)
    run_tier("TIER 2 - Important (Sub-pages)", TIER2, max_workers=3)

    # Clear cache again after tier 2
    cache.clear()

    # TIER 3 - Heavy cohorts (sequential, 1 worker to avoid lock contention)
    run_tier("TIER 3 - Heavy Cohorts (Sequential)", TIER3, max_workers=1)

    # Final cache clear
    cache.clear()

    total_elapsed = time.time() - total_start
    print("\n" + "=" * 60)
    print(f"  ALL MVs REFRESHED in {total_elapsed:.1f} seconds!")
    print(f"  Dashboard is fully up to date.")
    print("=" * 60)
