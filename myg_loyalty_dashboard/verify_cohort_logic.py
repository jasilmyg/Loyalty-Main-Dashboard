import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
django.setup()

from analytics.services import AnalyticsService

def verify_cohort_insights():
    service = AnalyticsService()
    print("Fetching Cohort Insights...")
    insights = service.get_cohort_business_insights()
    
    print(f"\nTotal Insights Generated: {len(insights)}")
    
    for i, insight in enumerate(insights, 1):
        print(f"\n--- Insight {i}: {insight['title']} ---")
        print(f"Observation: {insight['observation']}")
        print(f"Reason: {insight['reason']}")
        print(f"Impact: {insight['impact']}")
        print(f"Action: {insight['action']}")
        print(f"Priority: {insight['priority']}")
        
    # Validations
    has_momentum = any("Momentum" in i['title'] for i in insights)
    has_stabilization = any("Stabilization" in i['title'] for i in insights)
    has_quality = any("Quality" in i['title'] for i in insights)
    has_delayed = any("Delayed" in i['title'] for i in insights)
    has_plateau = any("Plateau" in i['title'] for i in insights)
    
    print("\nLogic Verification Check:")
    print(f" [OK] Year 1 Momentum logic check" if has_momentum else " [FAIL] Year 1 Momentum missing")
    print(f" [OK] Year 3 Stability logic check" if has_stabilization else " [FAIL] Year 3 Stability missing")
    print(f" [OK] Best/Worst Quality logic check" if has_quality else " [FAIL] Quality Variance missing")
    print(f" [OK] Delayed Return pattern check" if has_delayed else " [FAIL] Delayed Return missing")
    print(f" [OK] Stabilization Plateau check" if has_plateau else " [FAIL] Stabilization Plateau missing")

if __name__ == "__main__":
    verify_cohort_insights()
