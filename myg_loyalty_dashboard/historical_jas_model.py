"""
historical_jas_model.py
Computes historical JAS (Jul-Sep) repeat customer rates for 2023, 2024, 2025
and uses them to build a seasonality-adjusted projection for JAS 2026.
Also computes daily actuals breakdown for the burn-up chart.
Saves result to analytics/jas_cache.json
"""
import os, sys, json, django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.clickhouse_service import get_ch_client
from datetime import date, timedelta
from django.conf import settings

client = get_ch_client()

# ── Current JAS actuals (already known) ──────────────────────────
base_customers  = 5330462
jas_target      = 533046
jas_achieved    = 87927
jas_days_done   = (min(date.today(), date(2026,9,30)) - date(2026,7,1)).days + 1
jas_days_rem    = max(0, (date(2026,9,30) - date.today()).days)

# ── Historical JAS repeat rates ──────────────────────────────────
def get_jas_repeats(year):
    """Count customers who bought in JAS of `year` AND had a prior purchase."""
    start = f"{year}-07-01"
    end   = f"{year}-09-30"
    print(f"  Querying JAS {year} ({start} to {end})...")
    row = client.query(f"""
        SELECT countIf(in_jas = 1 AND has_prior = 1)
        FROM (
            SELECT customer_mobile,
                   maxIf(1, parsed_date >= toDate('{start}')
                            AND parsed_date <= toDate('{end}')) AS in_jas,
                   maxIf(1, parsed_date < toDate('{start}'))    AS has_prior
            FROM sales_data
            WHERE length(customer_mobile) = 10
              AND customer_mobile != ''
              AND parsed_date != toDate('1970-01-01')
            GROUP BY customer_mobile
        )
    """).result_rows
    return int(row[0][0]) if row else 0

def get_base(year):
    """Count unique customers up to June 30 of `year`."""
    cutoff = f"{year}-07-01"
    print(f"  Base customers up to Jun {year}...")
    row = client.query(f"""
        SELECT uniqExact(customer_mobile)
        FROM sales_data
        WHERE parsed_date < toDate('{cutoff}')
          AND parsed_date != toDate('1970-01-01')
          AND length(customer_mobile) = 10
          AND customer_mobile != ''
    """).result_rows
    return int(row[0][0]) if row else 0

print("=" * 60)
print("HISTORICAL JAS MODEL COMPUTATION")
print("=" * 60)

historical = {}
for yr in [2023, 2024, 2025]:
    print(f"\n[{yr}]")
    rpt  = get_jas_repeats(yr)
    base = get_base(yr)
    rate = round(rpt / base * 100, 2) if base else 0
    historical[yr] = {"repeats": rpt, "base": base, "rate_pct": rate}
    print(f"  Repeats : {rpt:,}")
    print(f"  Base    : {base:,}")
    print(f"  Rate    : {rate}%")

rates = [v["rate_pct"] for v in historical.values() if v["rate_pct"] > 0]
avg_rate = round(sum(rates) / len(rates), 2) if rates else 0
hist_projection = int(base_customers * avg_rate / 100)

print(f"\n{'='*60}")
print(f"Average historical JAS rate   : {avg_rate}%")
print(f"Historical model projection   : {hist_projection:,}")
print(f"Current run-rate projection   : 278,940")

# ── Daily actuals breakdown ─────────────────────────────────────
print("\nComputing daily actuals breakdown...")
daily_rows = client.query("""
    SELECT dt, count() AS daily_new
    FROM (
        SELECT customer_mobile,
               minIf(parsed_date, parsed_date >= toDate('2026-07-01')
                     AND parsed_date <= today()) AS dt,
               maxIf(1, parsed_date < toDate('2026-07-01')) AS has_prior
        FROM sales_data
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND parsed_date != toDate('1970-01-01')
          AND parsed_date >= toDate('2026-07-01')
        GROUP BY customer_mobile
        HAVING has_prior = 1
    )
    GROUP BY dt ORDER BY dt
""").result_rows

cumulative = 0
jas_daily_pts = []
for r in daily_rows:
    cumulative += int(r[1])
    jas_daily_pts.append({"date": str(r[0]), "cum": cumulative})
print(f"  Daily data points: {len(jas_daily_pts)}")

# ── Metrics ─────────────────────────────────────────────────────
# Use historical model as the primary projection
forecast_final = hist_projection
achieved_pct   = round(jas_achieved / jas_target * 100, 1)
gap            = max(0, jas_target - jas_achieved)
req_daily      = int(gap / jas_days_rem) if jas_days_rem else 0
daily_rate     = jas_achieved / jas_days_done if jas_days_done else 0
days_rem_pct   = round(jas_days_rem / 92 * 100)

if jas_days_rem <= 0:
    status, color = ("ACHIEVED", "#10B981") if jas_achieved >= jas_target else ("MISSED", "#EF4444")
elif forecast_final >= jas_target:
    status, color = "ON TRACK", "#10B981"
elif forecast_final >= jas_target * 0.85:
    status, color = "AT RISK",  "#F59E0B"
else:
    status, color = "BEHIND",   "#EF4444"

cache = {
    "base_customers":     base_customers,
    "jas_target":         jas_target,
    "jas_achieved":       jas_achieved,
    "jas_achieved_pct":   achieved_pct,
    "jas_gap":            gap,
    "jas_days_done":      jas_days_done,
    "jas_days_rem":       jas_days_rem,
    "jas_days_total":     92,
    "jas_daily_rate":     round(daily_rate, 0),
    "jas_req_daily":      req_daily,
    "jas_forecast_final": forecast_final,
    "jas_status_badge":   status,
    "jas_risk_color":     color,
    "jas_daily_json":     jas_daily_pts,
    "jas_target_json":    jas_target,
    "jas_days_rem_pct":   days_rem_pct,
    "historical_rates":   historical,
    "avg_hist_rate":      avg_rate,
    "computed_at":        str(date.today()),
}

cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'jas_cache.json')
with open(cache_path, 'w') as f:
    json.dump(cache, f, indent=2)

print(f"\njas_cache.json updated: {cache_path}")
print(f"  Historical projection : {forecast_final:,}")
print(f"  Status                : {status}")
print(f"  Daily data points     : {len(jas_daily_pts)}")
