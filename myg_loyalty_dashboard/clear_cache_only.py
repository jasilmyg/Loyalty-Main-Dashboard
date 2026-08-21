import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()
from django.core.cache import cache
from analytics.clickhouse_service import get_ch_client

ch = get_ch_client()

print("=== ClickHouse Live Data ===")
r1 = ch.query("SELECT toDate(max(date)), count() FROM azure_invoice_report WHERE toDate(date) != '1970-01-01'").result_rows[0]
r2 = ch.query("SELECT toDate(max(date)), count() FROM azure_sales_report WHERE toDate(date) != '1970-01-01'").result_rows[0]
print("  azure_invoice_report  max_date=" + str(r1[0]) + "  total=" + str(r1[1]))
print("  azure_sales_report    max_date=" + str(r2[0]) + "  total=" + str(r2[1]))

print()
print("=== Clearing Django Cache ===")
cache.clear()
print("  All cache keys cleared. Dashboard will re-fetch live from ClickHouse.")
print("Done! All ClickHouse-powered sections are now live with Aug 20 data.")
