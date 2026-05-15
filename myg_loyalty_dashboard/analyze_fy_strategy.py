"""
Build mv_fy_sales using mv_monthly_summary (already aggregated, fast) 
combined with mv_fy_members (has new/repeat member counts per FY).

Strategy:
- mv_monthly_summary has: month_date, Branch, Staff, RBM, BDM, revenue, invoices, customers
- mv_fy_members has: fy_year, total_members, new_members
- We aggregate mv_monthly_summary by FY to get total sales
- Use mv_fy_members for new vs repeat member counts
- New members' sales ≈ (new_members/total_members) * total_sale (approximation) 

Actually better approach: mv_customer_summary has total_spend and first_visit.
We can compute the global FY sales precisely using:
  - mv_monthly_summary GROUP BY FY for total_sale, total_customers (approx - unique per month only)
  - But we need DISTINCT customers per FY which requires the raw data.

The real question is: what data does the frontend need?
Looking at the output columns:
  - fy_label, total_sale_cr, yoy_sale_pct, new_member_sale_cr, repeat_member_sale_cr, repeat_sale_pct, asp

For TOTAL SALE: mv_monthly_summary can give us exact total revenue per FY
For CUSTOMERS (for ASP): mv_fy_members already has total_members per FY (exact)
For NEW vs REPEAT sale: we need to approximate from mv_fy_members new_members ratio

Let's build mv_fy_sales from mv_monthly_summary + mv_fy_members:
"""
import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from analytics.services import _q, _q1
from django.db import connection

# First, check what's in mv_monthly_summary
print("=== mv_monthly_summary sample ===")
rows = _q("SELECT * FROM mv_monthly_summary LIMIT 3")
for r in rows:
    print(f"  {r}")

# Check FY range in mv_monthly_summary
row = _q1("""
    SELECT 
        MIN(month_date), MAX(month_date),
        SUM(revenue), COUNT(DISTINCT month_date)
    FROM mv_monthly_summary
""")
print(f"\n  Date range: {row[0]} to {row[1]}, total_rev={row[2]:,.0f}, months={row[3]}")

# Check mv_fy_members
print("\n=== mv_fy_members ===")
rows = _q("SELECT * FROM mv_fy_members ORDER BY fy_year")
for r in rows:
    print(f"  FY {r[0]}: total={r[1]:,}, new={r[2]:,}")

# Now test building a fast FY sales query from mv_monthly_summary
print("\n=== Building mv_fy_sales from mv_monthly_summary ===")
t0 = time.time()
rows = _q("""
    SELECT
        CASE WHEN EXTRACT(MONTH FROM month_date) >= 4
             THEN EXTRACT(YEAR FROM month_date)
             ELSE EXTRACT(YEAR FROM month_date) - 1
        END::INTEGER AS fy_year,
        SUM(revenue)::FLOAT   AS total_sale,
        SUM(customers)::BIGINT AS total_customers_approx
    FROM mv_monthly_summary
    GROUP BY 1
    ORDER BY 1
""")
elapsed = time.time() - t0
print(f"  Time: {elapsed:.3f}s  Rows: {len(rows)}")
for r in rows:
    print(f"  FY {r[0]}: sale={r[1]/1e7:.2f}Cr  cust={r[2]:,}")
