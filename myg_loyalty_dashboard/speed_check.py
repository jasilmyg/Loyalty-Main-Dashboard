import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()
from django.core.cache import cache
from analytics.services import AnalyticsService
svc = AnalyticsService()

cache.delete('yearly_cohort_global')
print('Cold start (from MV):')
t0 = time.time()
r = svc.get_yearly_cohort_analysis()
ms = (time.time() - t0) * 1000
print(f'  {ms:.0f}ms  years={sorted(r.keys())}')
for yr in sorted(r.keys()):
    size = r[yr]['size']
    nrp = r[yr].get('nrp_pct', 0)
    y1 = r[yr].get('years', {}).get(1)
    ret = round(y1['retention'], 1) if y1 else '-'
    print(f'  {yr}: size={size:,}  NRP={nrp}%  Y1={ret}%')

print()
t0 = time.time()
svc.get_yearly_cohort_analysis()
print(f'Warm (cached): {(time.time()-t0)*1000:.1f}ms')
