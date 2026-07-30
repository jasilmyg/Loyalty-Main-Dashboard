"""
generate_jas_cache.py
Pre-computes JAS 2026 quarter data from ClickHouse and saves to jas_cache.json.
Run this script once (and periodically) to refresh the cache.
"""
import os, sys, json, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from datetime import date
import os
from django.conf import settings

client = get_ch_client()

today      = date.today()
jas_start  = date(2026, 7, 1)
jas_end    = date(2026, 9, 30)
days_done  = max(1, (min(today, jas_end) - jas_start).days + 1)
days_rem   = max(0, (jas_end - today).days)
days_total = 92

base_customers = 5330462
trend_rate_2026 = 8.95
jas_target = round(base_customers * 0.10)
jas_forecast_final = int(base_customers * trend_rate_2026 / 100)
print("Computing JAS actuals (optimized single-pass query)...")

# Single-pass query: much faster than two IN subqueries
row = client.query("""
    SELECT
        countIf(in_jas = 1 AND has_prior = 1) AS repeat_jas
    FROM (
        SELECT
            customer_mobile,
            maxIf(1, parsed_date >= toDate('2026-07-01') AND parsed_date <= today()) AS in_jas,
            maxIf(1, parsed_date < toDate('2026-07-01'))                             AS has_prior
        FROM sales_data
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND parsed_date != toDate('1970-01-01')
        GROUP BY customer_mobile
    )
""").result_rows
jas_achieved = int(row[0][0]) if row else 0
print(f"  Repeat achieved: {jas_achieved:,}")

# Daily cumulative (also optimized)
print("Computing daily burn-up...")
daily_rows = client.query("""
    SELECT first_jas_date AS dt, count() AS daily_new
    FROM (
        SELECT
            customer_mobile,
            minIf(parsed_date, parsed_date >= toDate('2026-07-01') AND parsed_date <= today()) AS first_jas_date,
            maxIf(1, parsed_date < toDate('2026-07-01')) AS has_prior
        FROM sales_data
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND parsed_date != toDate('1970-01-01')
        GROUP BY customer_mobile
        HAVING has_prior = 1 AND first_jas_date != toDate('1970-01-01')
    )
    GROUP BY dt ORDER BY dt
""").result_rows

cumulative = 0
jas_daily_pts = []
for r in daily_rows:
    cumulative += int(r[1])
    jas_daily_pts.append({"date": str(r[0]), "cum": cumulative})

# Compute metrics
daily_rate     = jas_achieved / days_done if days_done > 0 else 0
forecast_final = jas_forecast_final
achieved_pct   = round(jas_achieved / jas_target * 100, 1) if jas_target else 0
gap            = max(0, jas_target - jas_achieved)
req_daily      = int(gap / days_rem) if days_rem > 0 else 0

if days_rem <= 0:
    status_badge = "ACHIEVED" if jas_achieved >= jas_target else "MISSED"
    risk_color   = "#10B981"  if jas_achieved >= jas_target else "#EF4444"
elif forecast_final >= jas_target:
    status_badge, risk_color = "ON TRACK", "#10B981"
elif forecast_final >= jas_target * 0.85:
    status_badge, risk_color = "AT RISK",  "#F59E0B"
else:
    status_badge, risk_color = "BEHIND",   "#EF4444"

cache = {
    "jas_target":         jas_target,
    "jas_achieved":       jas_achieved,
    "jas_achieved_pct":   achieved_pct,
    "jas_gap":            gap,
    "jas_days_done":      days_done,
    "jas_days_rem":       days_rem,
    "jas_days_total":     days_total,
    "jas_daily_rate":     round(daily_rate, 0),
    "jas_req_daily":      req_daily,
    "jas_forecast_final": forecast_final,
    "jas_status_badge":   status_badge,
    "jas_risk_color":     risk_color,
    "jas_daily_json":     jas_daily_pts,
    "jas_target_json":    jas_target,
    "avg_hist_rate":      trend_rate_2026,
    "computed_at":        str(today),
}

cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'jas_cache.json')
with open(cache_path, 'w') as f:
    json.dump(cache, f, indent=2)

print(f"\nJAS 2026 Cache saved to: {cache_path}")
print(f"  Target          : {jas_target:,}")
print(f"  Achieved        : {jas_achieved:,}  ({achieved_pct}%)")
print(f"  Projected Final : {forecast_final:,}")
print(f"  Status          : {status_badge}")
print(f"  Daily pts       : {len(jas_daily_pts)}")
