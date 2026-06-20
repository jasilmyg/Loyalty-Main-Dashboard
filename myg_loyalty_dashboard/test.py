import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.services import _q
import traceback

try:
    rows = _q("""
        SELECT
            month_label,
            redeemed_customer_count,
            redeemed_point_value,
            redeemed_sale_value,
            pct_loyalty_discount,
            asp
        FROM mv_redemption_analysis
        ORDER BY month_start ASC
    """)
    print("Fetched rows:", len(rows))
    for r in rows:
        print(r)
except Exception as e:
    print("Error:", e)
    traceback.print_exc()
