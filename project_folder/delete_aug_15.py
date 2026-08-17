import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'myg_loyalty_dashboard')))
from analytics.clickhouse_service import get_ch_client

c = get_ch_client()
c.command("ALTER TABLE azure_sales_report DELETE WHERE toDate(date) = '2026-08-15'")
c.command("ALTER TABLE azure_invoice_report DELETE WHERE toDate(date) = '2026-08-15'")
c.command("ALTER TABLE azure_ingestion_log DELETE WHERE file_name LIKE '%15-08-2026%'")
print('Deleted Aug 15 data from tables and log')
