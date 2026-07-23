import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.core.cache import cache

print("Clearing all Django cache...")
cache.clear()
print("Done! All cached API responses cleared.")
print("Refresh your browser now to see fresh data.")
