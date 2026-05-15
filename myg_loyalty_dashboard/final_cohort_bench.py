import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.core.cache import cache
from analytics.services import AnalyticsService
svc = AnalyticsService()

cache.delete('yearly_cohort_global')
cache.delete('cohort_retention_global')

print('=== Cold start (from MV, no cache) ===')
t0 = time.time()
r = svc.get_yearly_cohort_analysis()
elapsed1 = (time.time() - t0) * 1000
print(f'  yearly_cohort: {elapsed1:.0f}ms  years={list(r.keys())}')
for yr in sorted(r.keys()):
    size = r[yr]['size']
    nrp  = r[yr].get('nrp_pct', 0)
    y1   = r[yr].get('years', {}).get(1)
    ret  = round(y1['retention'], 1) if y1 else 'N/A'
    print(f'  {yr}: size={size:,}  Y1_ret={ret}%  NRP={nrp}%')

t0 = time.time()
r2 = svc.get_cohort_retention()
elapsed2 = (time.time() - t0) * 1000
print(f'  cohort_retention: {elapsed2:.0f}ms  months={len(r2["cohorts"])}')

print()
print('=== Warm (cached) ===')
t0 = time.time(); svc.get_yearly_cohort_analysis()
print(f'  yearly_cohort: {(time.time()-t0)*1000:.0f}ms')
t0 = time.time(); svc.get_cohort_retention()
print(f'  cohort_retention: {(time.time()-t0)*1000:.0f}ms')
