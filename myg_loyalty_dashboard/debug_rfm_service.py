import os
import django
import sys

# Setup Django
sys.path.append(r'c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.services import AnalyticsService

service = AnalyticsService()
try:
    data = service.get_rfm_segments({})
    print("RFM Segments Data:")
    print(data)
except Exception as e:
    print(f"Error: {e}")
