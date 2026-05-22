from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/index.html'

class CustomerAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/customers.html'

class RFMView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/rfm.html'

class CohortView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/cohorts.html'

class PaymentView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/payments.html'

class DiscountView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/discounts.html'

class StaffView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/staff.html'

class BranchView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/branches.html'

class LoyaltyGapView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/loyalty_gap.html'

class RetailAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/retail_analytics.html'

class InvalidMobilesView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/invalid_mobiles.html'

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.holtwinters import ExponentialSmoothing

class TargetExecutiveView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/target_executive.html'
    
    def get_context_data(self, **kwargs):
        import os
        import json
        from django.conf import settings
        
        context = super().get_context_data(**kwargs)
        
        # 1. Try to load from PostgreSQL (works on all environments including Render)
        ai_data = None
        try:
            from analytics.models import ForecastCache
            ai_data = ForecastCache.get_lstm_cache()
            # get_lstm_cache returns {"KPIs": {}, ...} on DoesNotExist — treat as missing
            if not ai_data.get("KPIs"):
                ai_data = None
        except Exception as e:
            print(f"ForecastCache DB read failed: {e}")
            ai_data = None

        # 2. Fallback: local JSON file (for development convenience)
        if ai_data is None:
            cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'lstm_forecast_cache.json')
            try:
                with open(cache_path, 'r') as f:
                    ai_data = json.load(f)
                print("Loaded LSTM Forecast Cache from local JSON file (DB fallback).")
            except Exception as e:
                ai_data = {"KPIs": {}, "Charts": {}, "Insights": []}
                print(f"Failed to load LSTM Forecast Cache from DB and file: {e}")
            
        kpis = ai_data.get("KPIs", {})
        charts = ai_data.get("Charts", {})
        insights = ai_data.get("Insights", [])
        
        # Determine risk/commentary
        prob_target = kpis.get("Prob_Target", 0)
        
        if prob_target >= 95:
            risk_level = "HIGH CONFIDENCE"
            risk_color = "#10B981" # Emerald Green
            status_badge = "OPTIMAL"
        elif prob_target >= 85:
            risk_level = "MODERATE CONFIDENCE"
            risk_color = "#F59E0B" # Amber
            status_badge = "ON TRACK"
        else:
            risk_level = "LOW CONFIDENCE"
            risk_color = "#EF4444" # Red
            status_badge = "AT RISK"
            
        context.update(kpis)
        context.update({
            'risk_level': risk_level,
            'risk_color': risk_color,
            'status_badge': status_badge,
            'burn_json': json.dumps(charts.get("BurnUp", {})),
            'insights': insights
        })
        
        return context

class DBManagerView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'dashboard/db_manager.html'
    
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, *args, **kwargs):
        if 'sales_file' in request.FILES:
            upload_file = request.FILES['sales_file']
            try:
                import pandas as pd
                if upload_file.name.endswith('.csv'):
                    df = pd.read_csv(upload_file)
                else:
                    df = pd.read_excel(upload_file)
                    
                # Store original count for reporting
                original_count = len(df)
                
                # --- DATA HYGIENE FILTERS ---
                if 'Invoice Number' in df.columns:
                    # Remove any row where Invoice Number contains 'SMC/EI'
                    # fillna('') ensures we don't break on NaN values
                    df = df[~df['Invoice Number'].astype(str).str.contains('SMC/EI', na=False, case=False)]
                
                if 'Branch' in df.columns:
                    # Remove specific branches
                    df = df[~df['Branch'].astype(str).str.upper().str.strip().isin(['HEAD OFFICE', 'UG SMART CHOICE'])]
                    
                final_count = len(df)
                filtered_out = original_count - final_count
                    
                # Connect directly to PostgreSQL
                from django.conf import settings
                from sqlalchemy import create_engine
                
                db = settings.DATABASES['default']
                conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}?sslmode=require"
                engine = create_engine(conn_str)
                
                # Append directly to sales_data
                df.to_sql('sales_data', con=engine, if_exists='append', index=False)
                
                msg = f"Successfully uploaded {final_count:,} records into PostgreSQL."
                if filtered_out > 0:
                    msg += f" (Auto-filtered {filtered_out:,} records containing SMC/EI or invalid branches)."
                
                messages.success(request, msg)
            except Exception as e:
                messages.error(request, f"Error uploading data: {str(e)}")
                
        return redirect('db_manager')

class ReactDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/react_dashboard.html'

from django.contrib import messages
from django.shortcuts import redirect

class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/profile.html'
    
    def post(self, request, *args, **kwargs):
        user = request.user
        user.first_name = request.POST.get('first_name', '')
        user.last_name = request.POST.get('last_name', '')
        user.email = request.POST.get('email', '')
        user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('profile')

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone

class SecurityView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'dashboard/security.html'
    
    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()
        context['users'] = User.objects.all().order_by('username')
        return context
        
    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        
        if action == 'update_password':
            user_id = request.POST.get('user_id')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            if not all([user_id, new_password, confirm_password]):
                messages.error(request, 'All password fields are required.')
                return redirect('security')
                
            if new_password != confirm_password:
                messages.error(request, 'Passwords do not match.')
                return redirect('security')
                
            User = get_user_model()
            try:
                target_user = User.objects.get(id=user_id)
                target_user.set_password(new_password)
                target_user.save()
                messages.success(request, f'Password updated successfully for {target_user.username}.')
            except User.DoesNotExist:
                messages.error(request, 'User not found.')
                
        elif action == 'logout_all':
            # Clear all active sessions except the current one
            current_session_key = request.session.session_key
            if current_session_key:
                Session.objects.filter(expire_date__gte=timezone.now()).exclude(session_key=current_session_key).delete()
            else:
                Session.objects.all().delete()
            messages.success(request, 'All other sessions have been revoked successfully.')
            
        return redirect('security')

class LstmForecastView(LoginRequiredMixin, TemplateView):
    def get(self, request, *args, **kwargs):
        return redirect('target_executive')

from django.http import JsonResponse
from django.views import View

class PropensityForecastAPIView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        import os
        import json
        from django.conf import settings
        cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'propensity_cache.json')
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
            return JsonResponse(data)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


import threading
from django.db import connection

def rebuild_propensity_view_and_cache():
    try:
        with connection.cursor() as cursor:
            # Concurrently refresh the materialized view
            cursor.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_customer_propensity;")
            
        # Re-run propensity engine cache regeneration
        from analytics.customer_propensity_engine import generate_propensity_forecast
        generate_propensity_forecast()
    except Exception as e:
        print(f"Error rebuilding propensity materialized view or cache: {e}")


class CustomerPropensityView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/customer_propensity.html'
    
    def get_context_data(self, **kwargs):
        import os
        import json
        from django.conf import settings
        
        context = super().get_context_data(**kwargs)
        
        # Load Propensity Cache
        cache_path = os.path.join(settings.BASE_DIR, 'analytics', 'propensity_cache.json')
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            data = {"KPIs": {}, "Segments": {}, "Customer_Examples": []}
            print(f"Failed to load propensity cache: {e}")
            
        # Pre-calculate probability percentages to avoid math filters in template
        customer_examples = data.get("Customer_Examples", [])
        for cust in customer_examples:
            cust['prob_pct'] = round(float(cust.get('prob', 0.0)) * 100.0, 1)
            
        context.update({
            'kpis': data.get("KPIs", {}),
            'segments': data.get("Segments", {}),
            'customer_examples': customer_examples,
            'data_json': json.dumps(data)
        })
        
        return context


class CustomerPropensitySearchAPIView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        mobile = request.GET.get('mobile', '').strip()
        
        # Strip everything except digits and validate length
        mobile = ''.join(c for c in mobile if c.isdigit())
        if len(mobile) != 10:
            return JsonResponse({"error": "Invalid input. Please enter a valid 10-digit mobile number."}, status=400)
            
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT mobile, (probability/100.0)::double precision, recency::int, frequency::int, monetary::int
                    FROM mv_customer_propensity
                    WHERE mobile = %s
                    LIMIT 1;
                """, [mobile])
                row = cursor.fetchone()
                
            if not row:
                return JsonResponse({"error": f"No customer intelligence record found for mobile '{mobile}' in our 5.17M records."}, status=404)
                
            mobile, prob, recency, freq, monetary = row
            
            # Formulate strategic campaigns & intent tags
            if prob >= 0.7:
                intent_level = "High Intent"
                intent_badge = "bg-success-glow text-success"
                intent_desc = "Highly active customer. 70%+ chance of repeat purchase. Immediate target."
                strategic_action = f"🔥 **High Propensity Customer (Repeat Probability: {prob:.1%})**\n\nThis customer is primed and ready to purchase again! They last bought {recency} days ago, with a lifetime frequency of {freq} and a total spend of ₹{monetary:,}.\n\n🎯 **Tailored Marketing Action:** Send an exclusive high-value VIP voucher via SMS/WhatsApp with a 48-hour expiration to close the deal. Offer a complimentary accessory or extended warranty to maximize their average order value."
            elif prob >= 0.3:
                intent_level = "Medium Intent"
                intent_badge = "bg-warning-glow text-warning"
                intent_desc = "Moderate potential. 30%-70% chance of repeat. Needs nurturing."
                strategic_action = f"⚡ **Medium Propensity Customer (Repeat Probability: {prob:.1%})**\n\nThis customer has moderate intent, having made {freq} purchases with a lifetime value of ₹{monetary:,}. However, they are currently at day {recency} since their last visit, placing them at risk of slipping.\n\n🎯 **Tailored Marketing Action:** Send a personalized 'We Miss You' value-add offer. Highlight new product arrivals matching their past purchases and offer a conditional discount (e.g., ₹500 off on purchases above ₹5,000)."
            else:
                intent_level = "Low Intent"
                intent_badge = "bg-danger-glow text-danger"
                intent_desc = "Dormant/Inactive. <30% chance of repeat. Requires active reactivation."
                strategic_action = f"❄️ **Low Propensity Customer (Repeat Probability: {prob:.1%})**\n\nThis customer is currently dormant, with their last purchase {recency} days ago. They have a frequency of {freq} and lifetime value of ₹{monetary:,}.\n\n🎯 **Tailored Marketing Action:** Execute a win-back re-activation campaign. Offer an aggressive direct discount (e.g., flat 15% off) or invite them to trade in their old device for a new upgrade, triggering a fresh interest cycle."
                
            return JsonResponse({
                "success": True,
                "customer": {
                    "id": f"CUST-{mobile[-4:]}",
                    "mobile": mobile,
                    "prob": prob,
                    "recency": recency,
                    "freq": freq,
                    "monetary": monetary,
                    "intent_level": intent_level,
                    "intent_badge": intent_badge,
                    "intent_desc": intent_desc,
                    "strategic_action": strategic_action
                }
            })
        except Exception as e:
            return JsonResponse({"error": f"Database search failed: {str(e)}"}, status=500)


class CustomerPropensityRebuildAPIView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return JsonResponse({"error": "Unauthorized permission level required."}, status=403)
            
        # Spawn thread to rebuild view and regenerate cache concurrently
        thread = threading.Thread(target=rebuild_propensity_view_and_cache)
        thread.daemon = True
        thread.start()
        
        return JsonResponse({
            "success": True,
            "message": "AI Propensity Model retraining and background cache rebuild initialized. This takes approximately 2 minutes, and the dashboard will update automatically on completion."
        })


