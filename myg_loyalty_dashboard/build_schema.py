import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from ai_agent.services.schema_catalog import SchemaCatalogService

SchemaCatalogService.rebuild_catalog()

print("Test Search:")
res = SchemaCatalogService.search_relevant_tables("Which FUTURE stores from the 2024 cohort had the lowest resurrection count in 2026?", 2)
for r in res:
    print("-", r['table'])
