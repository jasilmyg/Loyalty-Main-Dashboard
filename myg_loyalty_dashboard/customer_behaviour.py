"""
D. Customer Behaviour
13. % of mobile buyers who subsequently buy another category
14. % of customers who return within 12 / 24 / 36 months
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, '.')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

TODAY = '2026-08-29'

# ── Q13: Mobile → Cross-Category Purchase ─────────────────────────────────────
print("=" * 70)
print("  Q13: Mobile Buyers → Cross-Category Conversion")
print("=" * 70)
print("  Fetching... (may take ~2 min)")

# Step 1: All customers who ever bought MOBILE + their first mobile purchase date
# Step 2: Check if they bought ANY other product category AFTER that date
r13 = ch.query("""
    WITH mobile_buyers AS (
        SELECT
            s.customer_mobile                AS mobile,
            min(toDate(s.date))              AS first_mobile_date
        FROM azure_invoice_report s
        INNER JOIN azure_sales_report sr ON s.invoice_no = sr.invoice_no
        INNER JOIN item_master m         ON sr.item_code = m.item_code
        WHERE m.product = 'MOBILE'
          AND length(trim(s.customer_mobile)) >= 10
          AND toDate(s.date) BETWEEN '2021-01-01' AND '2026-08-29'
          AND toDate(s.date) != '1970-01-01'
        GROUP BY s.customer_mobile
    ),
    cross_buyers AS (
        SELECT DISTINCT
            ai.customer_mobile AS mobile,
            m2.product         AS cross_product
        FROM azure_invoice_report ai
        INNER JOIN azure_sales_report sr2 ON ai.invoice_no = sr2.invoice_no
        INNER JOIN item_master m2         ON sr2.item_code = m2.item_code
        INNER JOIN mobile_buyers mb       ON ai.customer_mobile = mb.mobile
        WHERE m2.product != 'MOBILE'
          AND m2.product NOT IN ('SCHEME','GDOT CARE','D SPARE','OSG WARRANTY',
                                  'SERVICE','TOTAL SECURITY','LG AMC','SERVICE CHARGES',
                                  'DEMO','DEMO LAPTOP','DEMO ACCESSORIES','MYG DOMO',
                                  'MYG VERSE','DIY','CONTRACT WORK','CEGI')
          AND toDate(ai.date) > mb.first_mobile_date
          AND toDate(ai.date) != '1970-01-01'
          AND length(trim(ai.customer_mobile)) >= 10
    )
    SELECT
        cb.cross_product,
        count(DISTINCT cb.mobile) AS buyers
    FROM cross_buyers cb
    GROUP BY cb.cross_product
    ORDER BY buyers DESC
    LIMIT 20
""").result_rows

total_mobile_buyers = ch.query("""
    SELECT countDistinct(ai.customer_mobile)
    FROM azure_invoice_report ai
    INNER JOIN azure_sales_report sr ON ai.invoice_no = sr.invoice_no
    INNER JOIN item_master m         ON sr.item_code = m.item_code
    WHERE m.product = 'MOBILE'
      AND length(trim(ai.customer_mobile)) >= 10
      AND toDate(ai.date) BETWEEN '2021-01-01' AND '2026-08-29'
      AND toDate(ai.date) != '1970-01-01'
""").result_rows[0][0]

# Total who cross-bought at least one other category
total_cross = ch.query("""
    WITH mobile_buyers AS (
        SELECT
            ai.customer_mobile               AS mobile,
            min(toDate(ai.date))             AS first_mobile_date
        FROM azure_invoice_report ai
        INNER JOIN azure_sales_report sr ON ai.invoice_no = sr.invoice_no
        INNER JOIN item_master m         ON sr.item_code = m.item_code
        WHERE m.product = 'MOBILE'
          AND length(trim(ai.customer_mobile)) >= 10
          AND toDate(ai.date) BETWEEN '2021-01-01' AND '2026-08-29'
          AND toDate(ai.date) != '1970-01-01'
        GROUP BY ai.customer_mobile
    )
    SELECT countDistinct(ai2.customer_mobile)
    FROM azure_invoice_report ai2
    INNER JOIN azure_sales_report sr2 ON ai2.invoice_no = sr2.invoice_no
    INNER JOIN item_master m2         ON sr2.item_code = m2.item_code
    INNER JOIN mobile_buyers mb       ON ai2.customer_mobile = mb.mobile
    WHERE m2.product != 'MOBILE'
      AND m2.product NOT IN ('SCHEME','GDOT CARE','D SPARE','OSG WARRANTY',
                              'SERVICE','TOTAL SECURITY','LG AMC','SERVICE CHARGES',
                              'DEMO','DEMO LAPTOP','DEMO ACCESSORIES','MYG DOMO',
                              'MYG VERSE','DIY','CONTRACT WORK','CEGI')
      AND toDate(ai2.date) > mb.first_mobile_date
      AND toDate(ai2.date) != '1970-01-01'
      AND length(trim(ai2.customer_mobile)) >= 10
""").result_rows[0][0]

print(f"\n  Total Mobile Buyers (2021–2026)  : {total_mobile_buyers:>10,}")
print(f"  Mobile buyers who cross-shopped  : {total_cross:>10,}  ({total_cross/total_mobile_buyers*100:.1f}%)")
print(f"\n  Breakdown by next purchase category:")
print(f"  {'Category':<30} {'Customers':>12} {'% of Mobile Buyers':>20}")
print("  " + "-" * 64)
for row in r13:
    prod  = str(row[0])[:28]
    count = int(row[1])
    pct   = count / total_mobile_buyers * 100
    print(f"  {prod:<30} {count:>12,} {pct:>19.1f}%")

# ── Q14: Customer Return Rate ─────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("  Q14: Customer Return Rate (within 12 / 24 / 36 months)")
print("=" * 70)
print("  Fetching... (may take ~2 min)")

for months, cutoff in [(12, '2025-08-29'), (24, '2024-08-29'), (36, '2023-08-29')]:
    result = ch.query(f"""
        WITH first_purchase AS (
            SELECT
                customer_mobile,
                min(toDate(date)) AS first_date
            FROM azure_invoice_report
            WHERE length(trim(customer_mobile)) >= 10
              AND toDate(date) BETWEEN '2018-01-01' AND '{cutoff}'
              AND toDate(date) != '1970-01-01'
            GROUP BY customer_mobile
        ),
        returned AS (
            SELECT DISTINCT fp.customer_mobile
            FROM first_purchase fp
            INNER JOIN azure_invoice_report ai
                ON fp.customer_mobile = ai.customer_mobile
               AND toDate(ai.date) > fp.first_date
               AND toDate(ai.date) <= addMonths(fp.first_date, {months})
               AND toDate(ai.date) != '1970-01-01'
        )
        SELECT
            count(DISTINCT fp.customer_mobile)  AS total_customers,
            countDistinct(r.customer_mobile)    AS returned_customers
        FROM first_purchase fp
        LEFT JOIN returned r ON fp.customer_mobile = r.customer_mobile
    """).result_rows[0]

    total = int(result[0])
    returned = int(result[1])
    ret_pct  = returned / total * 100 if total > 0 else 0
    churn    = total - returned
    churn_pct = churn / total * 100 if total > 0 else 0

    print(f"\n  ── Return within {months} months (first purchase before {cutoff}) ──")
    print(f"    Total unique customers (cohort): {total:>10,}")
    print(f"    Returned within {months:>2} months     : {returned:>10,}  ({ret_pct:.1f}%)")
    print(f"    Did NOT return (churned)        : {churn:>10,}  ({churn_pct:.1f}%)")

print("\n" + "=" * 70)
