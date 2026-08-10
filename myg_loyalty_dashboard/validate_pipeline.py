"""Quick final validation of the 4-model pipeline."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import django; django.setup()

# Clear cache to force fresh build
cache_path = os.path.join('analytics', 'model_cache', 'campaign_intelligence.json')
if os.path.exists(cache_path):
    os.remove(cache_path)
    print("Cleared cache — forcing fresh build...")

from analytics.campaign_intelligence import build_campaign_intelligence
result = build_campaign_intelligence(force_rebuild=True)

print('=' * 55)
print('  FINAL DASHBOARD VALUES (4-MODEL PIPELINE)')
print('=' * 55)
print(f"  Resurrection Prob  : {result['resurrection_prob']}%  [LightGBM]")
print(f"  Repeat Purchase    : {result['repeat_prob']}%  [LightGBM]")
print(f"  Dormancy Risk      : {result['dormancy_risk']}%  [K-Means]")
print(f"  Predicted Vol (90d): {result['predicted_vol']:,}  [Prophet]")
print(f"  Prophet Accuracy   : {result['accuracy']}%")
print(f"  RMSE               : {result['rmse']:,}")
print(f"  LightGBM AUC       : {result['lgbm_auc']}")
print(f"  Data Source        : {result['data_source']}")
print(f"  Historical (last7) : {result['historical']}")
print(f"  Predictions Aug-Oct: {result['predictions']}")
print(f"  Upper Bound        : {result['upper_bound']}")
print(f"  Lower Bound        : {result['lower_bound']}")
print(f"  SHAP Insights      : {len(result['shap_insights'])} features")
print(f"  AI Insight Cards   : {len(result['insights'])}")
print()
print("  Insight Titles:")
for i, ins in enumerate(result['insights']):
    print(f"    [{i+1}] {ins['title'][:70]}")
print()
print("  Risk Tiers:")
for t, info in result.get('risk_tiers', {}).items():
    print(f"    {t:10s}: {info['pct']}%  ({info['count']:,} customers)")
print()
print("  Confidence Scores:")
for k, v in result.get('confidence_scores', {}).items():
    print(f"    {k}: {v}")
print('=' * 55)
print(f"Cache saved: {os.path.exists(cache_path)}")
