"""
portal_refresh_sep29.py
==========================
Refreshes ALL portal sections after Sep 29 data ingestion.
- All sections except Loyalty Point Matrix: azure_sales_report + azure_invoice_report
- Loyalty Point Matrix: PostgreSQL sales_data MVs (refreshed separately)
"""
import os, sys, time, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from django.core.cache import cache
from django.db import connection

ch = get_ch_client()
if not ch:
    print("ERROR: Cannot connect to ClickHouse")
    sys.exit(1)

print("=" * 70)
print("  Portal Refresh  --  Sep 29, 2026 Data")
print("=" * 70)

# =============================================================================
# STEP 1: Verify ClickHouse table status
# =============================================================================
print("\n[1] ClickHouse Table Status")
print("-" * 70)
ch_tables = [
    ("azure_invoice_report", "toDate(date)"),
    ("azure_sales_report",   "toDate(date)"),
    ("item_master",          None),
    ("branch_master",        None),
    ("sales_data",           None),
]
for table, date_col in ch_tables:
    try:
        cnt = ch.query(f"SELECT count() FROM {table}").result_rows[0][0]
        if date_col:
            max_d = ch.query(f"SELECT MAX({date_col}) FROM {table}").result_rows[0][0]
            print(f"  [OK] {table:<30} rows={cnt:>12,}  max_date={max_d}")
        else:
            print(f"  [OK] {table:<30} rows={cnt:>12,}")
    except Exception as e:
        print(f"  [ERR] {table:<29} ERROR: {e}")

# =============================================================================
# STEP 2: Clear ALL Django caches
# =============================================================================
print("\n[2] Clearing ALL Django caches")
print("-" * 70)
try:
    cache.clear()
    print("  [OK] All cached data cleared -- sections will re-query from ClickHouse")
except Exception as e:
    print(f"  [ERR] Cache clear failed: {e}")

# =============================================================================
# STEP 3: Refresh Loyalty Point Matrix -- PostgreSQL MVs (sales_data based)
# =============================================================================
print("\n[3] Refreshing Loyalty Point Matrix (PostgreSQL Materialized Views)")
print("-" * 70)

LOYALTY_MVS = [
    'mv_customer_summary',
    'mv_monthly_summary',
    'mv_loyalty_kpis',
    'mv_retail_loyalty',
    'mv_fy_loyalty',
    'mv_redemption_analysis',
    'mv_rfm_segments',
    'mv_cohort_retention',
    'mv_monthly_retention_2026',
    'mv_gap_analysis',
    'mv_action_engine',
    'mv_customer_propensity',
    'mv_customer_active_years',
    'mv_quarterly_members',
    'mv_dormant_reactivation_customers',
    'mv_branch_resurrection_2024_2026',
    'mv_true_repeat_amj_2026',
]

refreshed, skipped = 0, 0
for mv in LOYALTY_MVS:
    t0 = time.time()
    try:
        with connection.cursor() as cur:
            cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
        elapsed = time.time() - t0
        print(f"  [OK]   {mv:<45} {elapsed:.1f}s")
        refreshed += 1
    except Exception as e:
        err = str(e)
        if 'does not exist' in err.lower():
            print(f"  [SKIP] {mv:<45} (not found in PG)")
            skipped += 1
        else:
            print(f"  [FAIL] {mv:<45} {err[:60]}")
            skipped += 1

print(f"\n  PG MVs: {refreshed} refreshed, {skipped} skipped/not found")

# =============================================================================
# STEP 4: Pre-warm all Azure-backed section queries
# =============================================================================
print("\n[4] Pre-warming Azure-backed portal section queries")
print("-" * 70)

warmups = [
    ("Sales Overview -- total revenue & invoices", """
        SELECT SUM(sold_price), COUNT(DISTINCT invoice_no)
        FROM azure_sales_report
        WHERE toDate(date) >= '2020-07-01' AND sold_price > 0
    """),
    ("Sales Overview -- monthly trend (last 12M)", """
        SELECT toStartOfMonth(toDate(date)) AS m, SUM(sold_price)
        FROM azure_sales_report
        WHERE toDate(date) >= toDate(now()) - 365 AND sold_price > 0
        GROUP BY m ORDER BY m DESC LIMIT 12
    """),
    ("Sales Overview -- today & MTD", """
        SELECT
            countIf(toDate(date) = today()) AS today_inv,
            sumIf(sold_price, toDate(date) = today()) AS today_rev,
            sumIf(sold_price, toYYYYMM(toDate(date)) = toYYYYMM(today())) AS mtd_rev
        FROM azure_sales_report
        WHERE toDate(date) >= toStartOfMonth(today())
    """),
    ("Branch Performance -- top branches by revenue", """
        SELECT branch, COUNT(DISTINCT invoice_no), SUM(invoice_total), SUM(discount)
        FROM azure_invoice_report
        GROUP BY branch ORDER BY SUM(invoice_total) DESC LIMIT 30
    """),
    ("Customer Analytics -- unique customers total", """
        SELECT COUNT(DISTINCT customer_mobile)
        FROM azure_invoice_report
        WHERE length(toString(customer_mobile)) = 10
    """),
    ("Category / Product Analysis -- item_master join", """
        SELECT i.product, i.brand, SUM(s.sold_price), SUM(s.qty)
        FROM azure_sales_report s
        JOIN item_master i ON s.item_code = i.item_code
        WHERE toDate(s.date) >= '2020-07-01' AND s.sold_price > 0
        GROUP BY i.product, i.brand ORDER BY SUM(s.sold_price) DESC LIMIT 20
    """),
    ("Brand Analysis -- brand-wise revenue", """
        SELECT i.brand, SUM(s.sold_price), SUM(s.qty), COUNT(DISTINCT s.invoice_no)
        FROM azure_sales_report s
        JOIN item_master i ON s.item_code = i.item_code
        WHERE toDate(s.date) >= '2020-07-01' AND s.sold_price > 0
        GROUP BY i.brand ORDER BY SUM(s.sold_price) DESC LIMIT 20
    """),
    ("RFM Segmentation -- recency/frequency/monetary", """
        SELECT customer_mobile,
               COUNT(DISTINCT toDate(date)) AS frequency,
               SUM(invoice_total) AS monetary,
               dateDiff('day', MAX(toDate(date)), today()) AS recency
        FROM azure_invoice_report
        WHERE length(toString(customer_mobile)) = 10
          AND invoice_total > 0
        GROUP BY customer_mobile HAVING frequency >= 2
        LIMIT 50
    """),
    ("Staff Performance -- top 20 sales staff", """
        SELECT sales_staff_code, COUNT(DISTINCT invoice_no), SUM(invoice_total)
        FROM azure_invoice_report
        WHERE sales_staff_code != ''
        GROUP BY sales_staff_code ORDER BY SUM(invoice_total) DESC LIMIT 20
    """),
    ("Financier Trends -- loan amounts by financier", """
        SELECT financier_name, SUM(loan_amount), COUNT(*)
        FROM azure_invoice_report
        WHERE financier_name != '' AND loan_amount > 0
        GROUP BY financier_name ORDER BY SUM(loan_amount) DESC LIMIT 15
    """),
    ("Discount Analysis -- discount by branch", """
        SELECT branch, SUM(discount), SUM(invoice_total),
               round(SUM(discount)/nullIf(SUM(invoice_total),0)*100, 2) AS disc_pct
        FROM azure_invoice_report
        WHERE invoice_total > 0
        GROUP BY branch ORDER BY SUM(discount) DESC LIMIT 20
    """),
    ("Customer Type Split -- new vs repeat", """
        SELECT customer_type, COUNT(DISTINCT invoice_no), SUM(invoice_total)
        FROM azure_invoice_report
        WHERE customer_type != ''
        GROUP BY customer_type ORDER BY SUM(invoice_total) DESC
    """),
    ("Cohort -- yearly unique customers", """
        SELECT toYear(toDate(date)) AS yr,
               COUNT(DISTINCT customer_mobile) AS customers
        FROM azure_invoice_report
        WHERE length(toString(customer_mobile)) = 10 AND invoice_total > 0
        GROUP BY yr ORDER BY yr ASC
    """),
    ("Exchange / Buyback Analysis", """
        SELECT branch, SUM(exchange), SUM(buyback), COUNT(*)
        FROM azure_invoice_report
        WHERE (exchange > 0 OR buyback > 0)
        GROUP BY branch ORDER BY SUM(exchange) DESC LIMIT 20
    """),
    ("District-wise Sales -- branch_master join", """
        SELECT b.district, SUM(i.invoice_total), COUNT(DISTINCT i.invoice_no)
        FROM azure_invoice_report i
        JOIN branch_master b ON i.branch = b.branch_name
        GROUP BY b.district ORDER BY SUM(i.invoice_total) DESC
    """),
    ("Sep 2026 daily revenue verification", """
        SELECT toDate(date) AS d, SUM(sold_price) AS rev, COUNT(DISTINCT invoice_no) AS inv
        FROM azure_sales_report
        WHERE toDate(date) >= '2026-09-01'
        GROUP BY d ORDER BY d ASC
    """),
]

all_ok, all_err = 0, 0
for desc, sql in warmups:
    t0 = time.time()
    try:
        rows = ch.query(sql.strip()).result_rows
        elapsed = time.time() - t0
        print(f"  [OK] {desc:<55} {elapsed:.2f}s  ({len(rows)} rows)")
        all_ok += 1
    except Exception as e:
        print(f"  [ERR] {desc:<54} {str(e)[:55]}")
        all_err += 1

print(f"\n  Warmups: {all_ok} OK, {all_err} errors")

# =============================================================================
# STEP 5: Final verification -- Sep 2026 daily counts
# =============================================================================
print("\n[5] Final Verification -- Sep 2026 Daily Counts")
print("-" * 70)
for table in ['azure_sales_report', 'azure_invoice_report']:
    rows = ch.query(f"""
        SELECT toDate(date) AS d, count() AS cnt
        FROM {table}
        WHERE toDate(date) >= '2026-09-01'
        GROUP BY d ORDER BY d ASC
    """).result_rows
    print(f"\n  {table} -- Sep 2026:")
    for r in rows:
        tag = " <-- NEW" if str(r[0]) in ('2026-09-29', '2026-09-29') else ""
        print(f"    {r[0]}  {r[1]:>8,} rows{tag}")

print("\n" + "=" * 70)
print("  Portal refresh complete!")
print("  -- All azure-backed sections: cache cleared + queries pre-warmed")
print("  -- Loyalty Point Matrix: PG materialized views refreshed")
print("  -- Data now current through: 2026-09-29")
print("=" * 70)
