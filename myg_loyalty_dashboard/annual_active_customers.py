"""
Annual Active Customers — Last 2 years from today (2026-08-29)
  Year 1 (prev): 2024-08-29 to 2025-08-28
  Year 2 (curr): 2025-08-29 to 2026-08-29
Active customer = unique mobile number with at least 1 invoice in the period
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, '.')
django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

TODAY = '2026-08-29'
Y2_START, Y2_END = '2025-08-29', TODAY          # Current year (last 12 months)
Y1_START, Y1_END = '2024-08-29', '2025-08-28'   # Previous year

print("=" * 60)
print("  Annual Active Customers  (last 2 years from today)")
print("=" * 60)

for label, s, e in [
    ("Year 2  (Aug 2025 – Aug 2026)", Y2_START, Y2_END),
    ("Year 1  (Aug 2024 – Aug 2025)", Y1_START, Y1_END),
]:
    # Unique mobiles with at least 1 invoice
    r_total = ch.query(f"""
        SELECT countDistinct(customer_mobile)
        FROM azure_invoice_report
        WHERE toDate(date) BETWEEN '{s}' AND '{e}'
          AND length(trim(customer_mobile)) >= 10
    """).result_rows[0][0]

    # Repeat customers (bought more than once)
    r_repeat = ch.query(f"""
        SELECT countDistinct(customer_mobile)
        FROM (
            SELECT customer_mobile, count() AS txn_count
            FROM azure_invoice_report
            WHERE toDate(date) BETWEEN '{s}' AND '{e}'
              AND length(trim(customer_mobile)) >= 10
            GROUP BY customer_mobile
            HAVING txn_count >= 2
        )
    """).result_rows[0][0]

    # Total invoices in period
    r_inv = ch.query(f"""
        SELECT count(), countDistinct(invoice_no)
        FROM azure_invoice_report
        WHERE toDate(date) BETWEEN '{s}' AND '{e}'
    """).result_rows[0]

    r_new = r_total - r_repeat
    print(f"\n  {label}")
    print(f"    Period                  : {s}  →  {e}")
    print(f"    Total Invoices          : {r_inv[1]:>12,}")
    print(f"    ─────────────────────────────────────────")
    print(f"    Active Unique Customers : {r_total:>12,}")
    print(f"      ↳  New  (1 purchase)  : {r_new:>12,}  ({r_new/r_total*100:.1f}%)")
    print(f"      ↳  Repeat (2+ purch.) : {r_repeat:>12,}  ({r_repeat/r_total*100:.1f}%)")

# YoY change
print("\n" + "=" * 60)
y2 = ch.query(f"""
    SELECT countDistinct(customer_mobile)
    FROM azure_invoice_report
    WHERE toDate(date) BETWEEN '{Y2_START}' AND '{Y2_END}'
      AND length(trim(customer_mobile)) >= 10
""").result_rows[0][0]
y1 = ch.query(f"""
    SELECT countDistinct(customer_mobile)
    FROM azure_invoice_report
    WHERE toDate(date) BETWEEN '{Y1_START}' AND '{Y1_END}'
      AND length(trim(customer_mobile)) >= 10
""").result_rows[0][0]

yoy = (y2 - y1) / y1 * 100 if y1 > 0 else 0
print(f"  YoY Growth : {y2:,} vs {y1:,}  →  {yoy:+.1f}%")
print("=" * 60)
