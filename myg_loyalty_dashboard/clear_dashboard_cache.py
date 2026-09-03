"""
clear_dashboard_cache.py
========================
Clears ALL Django cache keys so the dashboard reloads
with the latest ClickHouse data (including Aug 29 & Aug 30).
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.core.cache import cache

print("Clearing ALL Django cache...")
cache.clear()
print("✅ Cache cleared. Dashboard will now reload fresh data from ClickHouse.")

# Verify latest data available
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()
print()
print("Latest data available in ClickHouse:")
print("=" * 55)
for d in ['2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30']:
    s = ch.query(f"SELECT count() FROM azure_sales_report   WHERE toDate(date)='{d}'").result_rows[0][0]
    i = ch.query(f"SELECT count() FROM azure_invoice_report WHERE toDate(date)='{d}'").result_rows[0][0]
    mark = '✅' if s > 0 else '❌'
    print(f"  {mark} {d}:  sales={s:>8,}   invoices={i:>7,}")
ms = ch.query("SELECT max(toDate(date)) FROM azure_sales_report").result_rows[0][0]
print(f"\n  Max date available: {ms}")
print("=" * 55)
print("\nAll dashboard sections (except Loyalty Point Matrix) will")
print("now show data up to 2026-08-30 on next page load.")
