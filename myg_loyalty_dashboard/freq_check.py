import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','myg_loyalty_dashboard.settings')
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
ch = get_ch_client()

rows = ch.query("""
    SELECT purchase_count, count() AS customers, sum(total_rev) AS revenue
    FROM (
        SELECT i.customer_mobile AS mob,
               countDistinct(s.invoice_no) AS purchase_count,
               sum(s.sold_price) AS total_rev
        FROM azure_sales_report s
        JOIN item_master m ON s.item_code = m.item_code
        JOIN azure_invoice_report i ON s.invoice_no = i.invoice_no
        WHERE m.brand = 'MY PARF' AND s.sold_price > 0
          AND toDate(s.date) != '1970-01-01'
          AND i.customer_mobile != '' AND length(i.customer_mobile) = 10
        GROUP BY i.customer_mobile
    )
    GROUP BY purchase_count
    ORDER BY purchase_count LIMIT 10
""").result_rows
for r in rows: print(r)
