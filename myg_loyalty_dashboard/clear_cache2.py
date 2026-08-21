import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myg_loyalty_dashboard.settings")
django.setup()
from django.core.cache import cache
cache.clear()
print("Django cache cleared.")
