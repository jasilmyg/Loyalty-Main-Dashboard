"""
generate_jas_cache.py
Pre-computes JAS 2026 quarter data from ClickHouse (azure_invoice_report) and saves to jas_cache.json.
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
from analytics.jas_lstm_forecaster import run_jas_bilstm_forecast

client = get_ch_client()

today      = date.today()
jas_start  = date(2026, 7, 1)
jas_end    = date(2026, 9, 30)
days_done  = max(1, (min(today, jas_end) - jas_start).days + 1)
days_rem   = max(0, (jas_end - today).days)
days_total = 92

# -- Base customers: all unique customers with ANY purchase before JAS (from azure)
print("Computing base customers from azure_invoice_report...")
base_row = client.query("""
    SELECT countDistinct(customer_mobile)
    FROM azure_invoice_report
    WHERE length(customer_mobile) = 10
      AND customer_mobile != ''
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND toDate(date) < toDate('2026-07-01')
      AND toDate(date) != toDate('1970-01-01')
      AND invoice_total > 0
""").result_rows
base_customers = int(base_row[0][0]) if base_row else 5292679
print(f"  Base customers: {base_customers:,}")

jas_target = round(base_customers * 0.10)

print("Computing JAS actuals from azure_invoice_report (single-pass query)...")

# Single-pass: count customers who visited in JAS AND had a prior purchase
row = client.query("""
    SELECT countIf(in_jas = 1 AND has_prior = 1) AS repeat_jas
    FROM (
        SELECT
            customer_mobile,
            maxIf(1, toDate(date) >= toDate('2026-07-01') AND toDate(date) <= today()) AS in_jas,
            maxIf(1, toDate(date) <  toDate('2026-07-01'))                             AS has_prior
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
        GROUP BY customer_mobile
    )
""").result_rows
jas_achieved = int(row[0][0]) if row else 0
print(f"  Repeat achieved: {jas_achieved:,}")

# Daily cumulative burn-up
print("Computing daily burn-up from azure_invoice_report...")
daily_rows = client.query("""
    SELECT first_jas_date AS dt, count() AS daily_new
    FROM (
        SELECT
            customer_mobile,
            minIf(toDate(date), toDate(date) >= toDate('2026-07-01') AND toDate(date) <= today()) AS first_jas_date,
            maxIf(1, toDate(date) < toDate('2026-07-01')) AS has_prior
        FROM azure_invoice_report
        WHERE length(customer_mobile) = 10
          AND customer_mobile != ''
          AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
          AND toDate(date) != toDate('1970-01-01')
          AND invoice_total > 0
        GROUP BY customer_mobile
        HAVING has_prior = 1 AND first_jas_date != toDate('1970-01-01')
    )
    GROUP BY dt ORDER BY dt
""").result_rows

cumulative = 0
jas_daily_pts = []
for r in daily_rows:
    cumulative += int(r[1])
    jas_daily_pts.append({"date": str(r[0]), "cum": cumulative, "daily_new": int(r[1])})

print("Computing deep learning BiLSTM forecast for the remaining JAS quarter...")
jas_lstm_pts = []
try:
    remaining_forecast, jas_lstm_pts = run_jas_bilstm_forecast(jas_daily_pts, days_rem)
    jas_forecast_final = jas_achieved + remaining_forecast
except Exception as e:
    print(f"  BiLSTM forecast failed: {e}. Falling back to statistical average.")
    jas_forecast_final = jas_achieved + int((jas_achieved / days_done if days_done > 0 else 0) * days_rem)

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
    "jas_lstm_json":      jas_lstm_pts,
    "jas_target_json":    jas_target,
    "base_customers":     base_customers,
    "avg_hist_rate":      "Deep Learning BiLSTM",
    "computed_at":        str(today),
    "data_source":        "azure_invoice_report",
}

cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'jas_cache.json')
with open(cache_path, 'w') as f:
    json.dump(cache, f, indent=2)

print(f"\nJAS 2026 Cache saved to: {cache_path}")
print(f"  Source          : azure_invoice_report")
print(f"  Base customers  : {base_customers:,}")
print(f"  Target (10%)    : {jas_target:,}")
print(f"  Achieved        : {jas_achieved:,}  ({achieved_pct}%)")
print(f"  Projected Final : {forecast_final:,}")
print(f"  Status          : {status_badge}")
print(f"  Daily pts       : {len(jas_daily_pts)}")
