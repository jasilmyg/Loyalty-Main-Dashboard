"""Update jas_cache with trend-adjusted projection (8.95% rate)."""
import json
from datetime import date

# Historical data (from historical_jas_model.py output)
# 2023: 7.24%,  2024: 7.83%,  2025: 8.38%
# Annual trend: +0.57%/yr  →  2026 projected rate: 8.38 + 0.57 = 8.95%

base_customers    = 5330462
trend_rate_2026   = 8.95          # % — trend-adjusted
jas_target        = round(base_customers * 0.10)   # 533,046 (10% target)
jas_forecast_final = int(base_customers * trend_rate_2026 / 100)  # 477,076

jas_achieved      = 87927
jas_days_done     = (min(date.today(), date(2026,9,30)) - date(2026,7,1)).days + 1
jas_days_rem      = max(0, (date(2026,9,30) - date.today()).days)
daily_rate        = jas_achieved / jas_days_done if jas_days_done else 0
achieved_pct      = round(jas_achieved / jas_target * 100, 1)
gap               = max(0, jas_target - jas_achieved)
req_daily         = int(gap / jas_days_rem) if jas_days_rem else 0
days_rem_pct      = round(jas_days_rem / 92 * 100)

if jas_days_rem <= 0:
    status, color = ("ACHIEVED", "#10B981") if jas_achieved >= jas_target else ("MISSED", "#EF4444")
elif jas_forecast_final >= jas_target:
    status, color = "ON TRACK", "#10B981"
elif jas_forecast_final >= jas_target * 0.85:
    status, color = "AT RISK",  "#F59E0B"
else:
    status, color = "BEHIND",   "#EF4444"

path = r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics\jas_cache.json"
with open(path, "r") as f:
    cache = json.load(f)

cache["jas_forecast_final"] = jas_forecast_final
cache["jas_status_badge"]   = status
cache["jas_risk_color"]     = color
cache["avg_hist_rate"]      = trend_rate_2026
cache["jas_days_done"]      = jas_days_done
cache["jas_days_rem"]       = jas_days_rem
cache["jas_days_rem_pct"]   = days_rem_pct
cache["jas_daily_rate"]     = round(daily_rate, 0)
cache["jas_req_daily"]      = req_daily
cache["computed_at"]        = str(date.today())

with open(path, "w") as f:
    json.dump(cache, f, indent=2)

print("Cache updated with trend-adjusted projection")
print(f"  Trend rate (2026)  : {trend_rate_2026}%  (YoY +0.57%)")
print(f"  Projected Final    : {jas_forecast_final:,}")
print(f"  Target             : {jas_target:,}")
print(f"  Status             : {status}")
