"""
portal_refresh_oct13.py
=======================
Refreshes ALL portal sections after the Oct 10-12 2026 azure_sales_report /
azure_invoice_report load.
- Every section except the Loyalty Point Matrix reads the two azure tables.
- Loyalty Point Matrix reads sales_data (NOT touched here).
- Clears the shared FileBasedCache + the derived AI caches, then rebuilds the
  Campaign Intelligence model (now sourced from azure_invoice_report).
"""
import os, sys, io, time, warnings
warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django; django.setup()

from django.core.cache import cache
from analytics.clickhouse_service import get_ch_client

BASE = os.path.dirname(os.path.abspath(__file__))
ch = get_ch_client()

print("[1] Azure table status")
for t in ('azure_sales_report', 'azure_invoice_report'):
    n, mx = ch.query(f"SELECT count(), max(toDate(date)) FROM {t}").result_rows[0]
    print(f"   {t:<24} rows={n:>12,}  last_date={mx}")

print("[2] Clearing Django cache (file-based, shared with the running server)")
cache.clear()
print("   done")

print("[3] Removing derived caches that depend on the transaction data")
for p in (
    os.path.join(BASE, 'analytics', 'model_cache', 'campaign_intelligence.json'),
    os.path.join(BASE, 'analytics', 'neural_intelligence_cache.json'),
    os.path.join(BASE, 'analytics', 'propensity_cache.json'),
    os.path.join(BASE, 'analytics', 'ai_forecast_cache.json'),
):
    if os.path.exists(p):
        os.remove(p); print("   [DEL]", os.path.basename(p))
    else:
        print("   [--] ", os.path.basename(p), "(not present)")
print("   (JAS Q2 and LSTM forecast files kept: Jul-Sep quarter is closed)")

print("[4] Rebuilding Campaign Intelligence from azure_invoice_report")
t0 = time.time()
try:
    from analytics.campaign_intelligence import build_campaign_intelligence
    res = build_campaign_intelligence(force_rebuild=True)
    print(f"   [OK] rebuilt in {time.time()-t0:.0f}s; keys={list(res.keys())[:8] if isinstance(res, dict) else type(res)}")
except Exception as e:
    import traceback; traceback.print_exc()
    print("   [ERR]", str(e)[:200])

print("[5] Final daily check (last 5 days)")
for t, c in (('azure_sales_report', 'sold_price'), ('azure_invoice_report', 'invoice_total')):
    rows = ch.query(f"SELECT toDate(date) d, count(), round(sum({c})) FROM {t} WHERE date >= '2026-10-08' GROUP BY d ORDER BY d").result_rows
    print("  ", t, [(str(r[0]), r[1], r[2]) for r in rows])
print("DONE")
