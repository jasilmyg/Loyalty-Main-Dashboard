"""
Cache Warm-up Script — Pre-answers the 30 most common AI queries
so the first request is always instant.

Run this: python warmup_ai_cache.py
Or call it from a cron job / management command.
"""
import os, sys, time, json, hashlib
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django
django.setup()

from django.core.cache import cache
from django.db import connection

# ─── Fast SQL queries that should always be pre-cached ───────────────────────
WARM_QUERIES = [
    # Cross-year cohorts (uses mv_cohort_cross_year — instant after MV built)
    ("unique customer count whose purchase in 2024 but not purchase in 2026",
     "SELECT COUNT(*) AS unique_customer_count FROM mv_cohort_cross_year WHERE in_2024 = 1 AND in_2026 = 0;"),

    ("unique customer count whose purchase in 2023 but not purchase in 2026",
     "SELECT COUNT(*) AS unique_customer_count FROM mv_cohort_cross_year WHERE in_2023 = 1 AND in_2026 = 0;"),

    ("unique customer count whose purchase in 2022 but not purchase in 2026",
     "SELECT COUNT(*) AS unique_customer_count FROM mv_cohort_cross_year WHERE in_2022 = 1 AND in_2026 = 0;"),

    ("customers who purchased in 2024 and also in 2026",
     "SELECT COUNT(*) AS unique_customer_count FROM mv_cohort_cross_year WHERE in_2024 = 1 AND in_2026 = 1;"),

    ("customers who purchased in 2023 and also in 2026",
     "SELECT COUNT(*) AS unique_customer_count FROM mv_cohort_cross_year WHERE in_2023 = 1 AND in_2026 = 1;"),

    # Dormant customers by year
    ("dormant customers 2024",
     "SELECT COALESCE(SUM(unique_customers),0) AS dormant_customers FROM mv_dormant_reactivation WHERE cohort_year = 2024 AND first_2026_month IS NULL;"),
    ("dormant customers 2023",
     "SELECT COALESCE(SUM(unique_customers),0) AS dormant_customers FROM mv_dormant_reactivation WHERE cohort_year = 2023 AND first_2026_month IS NULL;"),
    ("dormant customers 2022",
     "SELECT COALESCE(SUM(unique_customers),0) AS dormant_customers FROM mv_dormant_reactivation WHERE cohort_year = 2022 AND first_2026_month IS NULL;"),

    # Total customers
    ("total unique customers",
     "SELECT COUNT(DISTINCT mobile) AS total_customers FROM mv_customer_dates;"),
    ("total customers in 2024",
     "SELECT COUNT(*) AS unique_customers FROM mv_yearly_customer_cohorts WHERE purchase_year = 2024;"),
    ("total customers in 2025",
     "SELECT COUNT(*) AS unique_customers FROM mv_yearly_customer_cohorts WHERE purchase_year = 2025;"),
    ("total customers in 2026",
     "SELECT COUNT(*) AS unique_customers FROM mv_yearly_customer_cohorts WHERE purchase_year = 2026;"),

    # Revenue by year
    ("total revenue 2024",
     "SELECT COALESCE(SUM(revenue),0) AS annual_revenue, COALESCE(SUM(invoices),0) AS annual_invoices FROM mv_monthly_summary WHERE EXTRACT(YEAR FROM month_date) = 2024;"),
    ("total revenue 2025",
     "SELECT COALESCE(SUM(revenue),0) AS annual_revenue, COALESCE(SUM(invoices),0) AS annual_invoices FROM mv_monthly_summary WHERE EXTRACT(YEAR FROM month_date) = 2025;"),
    ("total revenue 2026",
     "SELECT COALESCE(SUM(revenue),0) AS annual_revenue, COALESCE(SUM(invoices),0) AS annual_invoices FROM mv_monthly_summary WHERE EXTRACT(YEAR FROM month_date) = 2026;"),

    # RFM segments
    ("rfm segments",
     "SELECT segment, COUNT(*) AS customer_count FROM mv_rfm_segments GROUP BY segment ORDER BY customer_count DESC;"),

    # Top branches
    ("top 10 branches",
     'SELECT "Branch", SUM(revenue) AS revenue, SUM(invoices) AS invoices FROM mv_monthly_summary WHERE month_date = DATE_TRUNC(\'month\', CURRENT_DATE) GROUP BY "Branch" ORDER BY revenue DESC LIMIT 10;'),

    # Resurrection rate
    ("resurrection rate by branch",
     "SELECT branch_name, resurrected_customers, cohort_size, resurrection_rate FROM mv_branch_resurrection_2024_2026 ORDER BY resurrected_customers DESC LIMIT 10;"),

    # Retention 2026
    ("monthly retention 2026",
     "SELECT month_label, month_start, unique_customers, total_sales FROM mv_monthly_retention_2026 ORDER BY month_start;"),

    # Year-over-year breakdown
    ("customers per year",
     "SELECT purchase_year, COUNT(*) AS unique_customers, SUM(purchase_count) AS total_purchases, ROUND(AVG(total_spend)::numeric, 2) AS avg_spend FROM mv_yearly_customer_cohorts GROUP BY purchase_year ORDER BY purchase_year;"),
]


def make_cache_key(prompt: str) -> str:
    return "ai_warmup_" + hashlib.md5(prompt.lower().strip().encode()).hexdigest()


def execute_query(sql: str) -> list:
    with connection.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]


def format_result(results: list) -> str:
    if not results:
        return "No data found."
    row = results[0]
    if len(results) == 1:
        lines = []
        for k, v in row.items():
            label = k.replace('_', ' ').title()
            if isinstance(v, float):
                fmt = f"Rs.{v:,.2f}" if any(w in k.lower() for w in ['revenue','spend','value','amount']) else f"{v:,.2f}"
            elif isinstance(v, int):
                fmt = f"Rs.{v:,}" if any(w in k.lower() for w in ['revenue','spend','value','amount']) else f"{v:,}"
            else:
                fmt = str(v) if v is not None else "—"
            lines.append(f"**{label}:** {fmt}")
        return "\n".join(lines)
    else:
        headers = list(results[0].keys())
        table = ["| " + " | ".join(h.replace('_',' ').title() for h in headers) + " |"]
        table.append("|" + "---|" * len(headers))
        for r in results:
            cells = []
            for h in headers:
                v = r[h]
                if isinstance(v, (int, float)) and any(w in h.lower() for w in ['revenue','spend','value','amount']):
                    cells.append(f"Rs.{v:,.0f}" if v else "0")
                elif isinstance(v, (int, float)):
                    cells.append(f"{v:,}" if v else "0")
                else:
                    cells.append(str(v) if v else "—")
            table.append("| " + " | ".join(cells) + " |")
        return "\n".join(table)


print("=" * 60)
print("  AI Cache Warm-Up — Pre-caching top queries")
print("=" * 60)

success = 0
failed  = 0

for prompt, sql in WARM_QUERIES:
    key = make_cache_key(prompt)
    t0  = time.time()
    try:
        results = execute_query(sql)
        message = format_result(results) + "\n\n*Pre-cached result (FastPath Engine)*"
        cached_response = {
            "message": message,
            "charts":  [],
            "kpis":    [],
        }
        cache.set(key, cached_response, timeout=3600 * 12)  # 12-hour cache
        elapsed = time.time() - t0
        print(f"  [OK] {prompt[:55]:<55} ({elapsed:.2f}s) | Result: {results[0] if results else 'empty'}")
        success += 1
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [FAIL] {prompt[:55]:<55} ({elapsed:.2f}s) | Error: {e}")
        failed += 1

print("\n" + "=" * 60)
print(f"  Cached:  {success} queries")
print(f"  Failed:  {failed} queries")
print("=" * 60)
