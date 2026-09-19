"""
refresh_portal_azure_only.py
=============================
Refreshes all portal sections using ONLY ClickHouse Azure tables:
  - azure_invoice_report   (invoice-level: branch, customer, revenue, discount)
  - azure_sales_report     (item-level: item_code, qty, sold_price)
  - item_master            (item metadata: brand, category, product)
  - branch_master          (branch metadata: district, rbm, bdm, store_type)

NO PostgreSQL materialized view refresh needed.
Loyalty Point Matrix continues to use sales_data table (as per requirement).
"""
import os, sys, time, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from django.core.cache import cache

ch = get_ch_client()
if not ch:
    print("ERROR: Cannot connect to ClickHouse")
    sys.exit(1)

print("=" * 70)
print("  Portal Refresh — ClickHouse Azure Tables Only")
print("=" * 70)

# ── Step 1: Verify all 4 tables are current ──────────────────────────────────
print("\n[1] ClickHouse Table Status")
print("-" * 70)
tables_status = [
    ("azure_invoice_report", "toDate(date)"),
    ("azure_sales_report",   "toDate(date)"),
    ("item_master",          None),
    ("branch_master",        None),
]
all_ok = True
for table, date_col in tables_status:
    try:
        cnt = ch.query(f"SELECT count() FROM {table}").result_rows[0][0]
        if date_col:
            max_d = ch.query(f"SELECT MAX({date_col}) FROM {table}").result_rows[0][0]
            print(f"  ✓ {table:<30} rows={cnt:>12,}  max_date={max_d}")
        else:
            print(f"  ✓ {table:<30} rows={cnt:>12,}")
    except Exception as e:
        print(f"  ✗ {table:<30} ERROR: {e}")
        all_ok = False

# ── Step 2: Clear ALL Django caches ──────────────────────────────────────────
print("\n[2] Clearing ALL Django caches")
print("-" * 70)
try:
    cache.clear()
    print("  ✓ All cached data cleared — sections will re-query from ClickHouse")
except Exception as e:
    print(f"  ✗ Cache clear failed: {e}")

# ── Step 3: Pre-warm key portal queries ───────────────────────────────────────
print("\n[3] Pre-warming key portal section queries")
print("-" * 70)

warmups = [
    # (description, sql)
    ("Sales Overview — total revenue & invoices", """
        SELECT SUM(sold_price), COUNT(DISTINCT invoice_no)
        FROM azure_sales_report
        WHERE toDate(date) >= '2020-07-01' AND sold_price > 0
    """),
    ("Sales Overview — monthly trend (last 12M)", """
        SELECT toStartOfMonth(toDate(date)) AS month_start,
               formatDateTime(toStartOfMonth(toDate(date)), '%b %y') AS m,
               SUM(sold_price) AS rev
        FROM azure_sales_report
        WHERE toDate(date) >= toDate(now()) - 365 AND sold_price > 0
        GROUP BY month_start, m ORDER BY month_start DESC LIMIT 12
    """),
    ("Customer Analytics — unique/repeat customers", """
        SELECT COUNT(DISTINCT customer_mobile),
               countDistinctIf(customer_mobile, invoice_total > 0)
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
    """),
    ("Branch Performance — top 20 branches", """
        SELECT branch, COUNT(DISTINCT invoice_no), SUM(invoice_total), SUM(discount)
        FROM azure_invoice_report
        GROUP BY branch ORDER BY SUM(invoice_total) DESC LIMIT 20
    """),
    ("Category Analysis — by azure_sales_report", """
        SELECT extract(item_code, '^([A-Za-z]+)') AS prefix,
               SUM(sold_price), SUM(qty)
        FROM azure_sales_report
        WHERE toDate(date) != toDate('1970-01-01') AND sold_price > 0
        GROUP BY prefix ORDER BY SUM(sold_price) DESC LIMIT 25
    """),
    ("Category Analysis — item_master join", """
        SELECT i.product, SUM(s.sold_price), SUM(s.qty)
        FROM azure_sales_report s
        JOIN item_master i ON s.item_code = i.item_code
        WHERE toDate(s.date) != toDate('1970-01-01') AND s.sold_price > 0
        GROUP BY i.product ORDER BY SUM(s.sold_price) DESC LIMIT 15
    """),
    ("Brand Analysis — item_master join", """
        SELECT i.brand, SUM(s.sold_price), SUM(s.qty)
        FROM azure_sales_report s
        JOIN item_master i ON s.item_code = i.item_code
        WHERE toDate(s.date) != toDate('1970-01-01') AND s.sold_price > 0
        GROUP BY i.brand ORDER BY SUM(s.sold_price) DESC LIMIT 15
    """),
    ("Branch Master — district-wise branches", """
        SELECT b.district, COUNT(*) as branch_count
        FROM branch_master b GROUP BY b.district ORDER BY branch_count DESC
    """),
    ("Gap Segmentation — purchase gaps", """
        SELECT customer_mobile,
               COUNT(DISTINCT toDate(date)) AS visits,
               MAX(toDate(date)) AS last_visit
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) >= '2024-01-01'
        GROUP BY customer_mobile
        LIMIT 100
    """),
    ("RFM Segments — azure_invoice_report", """
        SELECT customer_mobile,
               COUNT(DISTINCT toDate(date)) AS frequency,
               SUM(invoice_total) AS monetary,
               dateDiff('day', MAX(toDate(date)), today()) AS recency
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND invoice_total > 0
        GROUP BY customer_mobile
        HAVING frequency >= 2
        LIMIT 100
    """),
    ("Financier Trends — loan amounts", """
        SELECT financier_name, SUM(loan_amount), COUNT(*)
        FROM azure_invoice_report
        WHERE financier_name != '' AND loan_amount > 0
        GROUP BY financier_name ORDER BY SUM(loan_amount) DESC LIMIT 10
    """),
    ("Unique Branches list", """
        SELECT DISTINCT branch FROM azure_invoice_report
        WHERE branch != '' ORDER BY branch ASC
    """),
    ("Staff Performance — top 20", """
        SELECT sales_staff_code, COUNT(DISTINCT invoice_no), SUM(invoice_total)
        FROM azure_invoice_report
        WHERE sales_staff_code != ''
        GROUP BY sales_staff_code ORDER BY SUM(invoice_total) DESC LIMIT 20
    """),
    ("Cohort Retention — yearly", """
        SELECT toYear(toDate(date)) AS yr,
               COUNT(DISTINCT customer_mobile) AS customers
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND invoice_total > 0
        GROUP BY yr ORDER BY yr ASC
    """),
]

for desc, sql in warmups:
    t0 = time.time()
    try:
        rows = ch.query(sql.strip()).result_rows
        elapsed = time.time() - t0
        print(f"  ✓ {desc:<55} {elapsed:.2f}s  ({len(rows)} rows)")
    except Exception as e:
        print(f"  ✗ {desc:<55} ERROR: {str(e)[:60]}")

# ── Step 4: Final verification ─────────────────────────────────────────────────
print("\n[4] Final Data Verification")
print("-" * 70)
for table, date_col in [("azure_invoice_report", "toDate(date)"),
                         ("azure_sales_report", "toDate(date)")]:
    rows = ch.query(f"""
        SELECT {date_col} AS d, count() AS cnt
        FROM {table}
        WHERE {date_col} >= '2026-09-01'
        GROUP BY d ORDER BY d ASC
    """).result_rows
    print(f"\n  {table} — Sep 2026 daily counts:")
    for r in rows:
        print(f"    {r[0]}  →  {r[1]:,} rows")

print("\n" + "=" * 70)
print("  ✓ Portal refresh complete!")
print("  All sections now serve data from ClickHouse Azure tables.")
print("  Loyalty Point Matrix uses sales_data table (unchanged).")
print("=" * 70)
