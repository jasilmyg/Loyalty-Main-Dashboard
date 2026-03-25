import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.services import AnalyticsService

def verify_api_logic():
    service = AnalyticsService()
    print(f"Using Native (DuckDB): {service.using_native}")
    
    # Simulate empty filters as in the screenshot
    data = service.get_sales_overview({})
    
    print("\nAPI Response Logic Result:")
    print(f"Total Revenue: {data.get('total_revenue')}")
    print(f"Total Invoices: {data.get('total_invoices')}")
    print(f"ATV: {data.get('atv')}")
    print(f"Monthly Trend Length: {len(data.get('monthly_trend', []))}")
    if data.get('monthly_trend'):
        print(f"First Month: {data['monthly_trend'][0]}")
        print(f"Last Month: {data['monthly_trend'][-1]}")

if __name__ == "__main__":
    verify_api_logic()
