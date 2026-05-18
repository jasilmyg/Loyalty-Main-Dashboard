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
        context = super().get_context_data(**kwargs)
        
        # Business Logic Constants
        TOTAL_DB = 5033297
        TARGET_PCT = 0.08
        TARGET_COUNT = int(TOTAL_DB * TARGET_PCT)

        HIST_START = pd.to_datetime("2020-01-01")
        HIST_END = pd.to_datetime("2026-03-31")
        AMJ_START = pd.to_datetime("2026-04-01")
        ACTUALS_END = pd.to_datetime("2026-05-10")
        AMJ_END = pd.to_datetime("2026-06-30")

        TOTAL_AMJ_DAYS = (AMJ_END - AMJ_START).days + 1
        ELAPSED_DAYS = (ACTUALS_END - AMJ_START).days + 1
        REMAINING_DAYS = TOTAL_AMJ_DAYS - ELAPSED_DAYS
        TARGET_RUN_RATE_DAILY = TARGET_COUNT / TOTAL_AMJ_DAYS
        
        # Synthetic Data for Historical
        np.random.seed(42)
        dates_hist = pd.date_range(start=HIST_START, end=HIST_END, freq='ME')
        base_customers = np.linspace(50000, 300000, len(dates_hist))
        seasonality = np.sin(np.arange(len(dates_hist)) * (2 * np.pi / 12)) * 50000
        noise = np.random.normal(0, 10000, len(dates_hist))
        active_customers = base_customers + seasonality + noise
        repeat_customers = active_customers * np.random.uniform(0.65, 0.75, len(dates_hist))
        new_customers = active_customers - repeat_customers
        
        hist_df = pd.DataFrame({
            'Date': dates_hist,
            'Active': active_customers.astype(int),
            'Repeat': repeat_customers.astype(int),
            'New': new_customers.astype(int)
        })
        
        # Synthetic Data for AMJ Actuals (Scaled to exact Real Data: 183,831)
        dates_amj = pd.date_range(start=AMJ_START, end=ACTUALS_END, freq='D')
        daily_actuals = np.random.normal(loc=4595, scale=800, size=len(dates_amj))
        for i, d in enumerate(dates_amj):
            if d.weekday() >= 5:
                daily_actuals[i] *= 1.3
                
        # Scale to exactly 183831
        scaling_factor = 183831 / np.sum(daily_actuals)
        daily_actuals = np.round(daily_actuals * scaling_factor).astype(int)
        
        # Fix any rounding differences
        diff = 183831 - np.sum(daily_actuals)
        daily_actuals[-1] += diff
                
        amj_df = pd.DataFrame({
            'Date': dates_amj,
            'Daily_Achieved': daily_actuals
        })
        amj_df['Cumulative_Achieved'] = amj_df['Daily_Achieved'].cumsum()
        
        current_achieved = 183831
        remaining_target = TARGET_COUNT - current_achieved
        current_pct = current_achieved / TARGET_COUNT
        current_run_rate = current_achieved / ELAPSED_DAYS
        req_run_rate = remaining_target / REMAINING_DAYS
        pace_status = "Behind Target" if current_run_rate < req_run_rate else "On Track"
        
        # Forecasts
        y = amj_df['Cumulative_Achieved'].values
        X = np.arange(len(y)).reshape(-1, 1)
        
        lr = LinearRegression()
        lr.fit(X, y)
        X_future = np.arange(len(y), len(y) + REMAINING_DAYS).reshape(-1, 1)
        lr_pred = lr.predict(X_future)
        
        ma_7 = amj_df['Daily_Achieved'].tail(7).mean()
        ma_pred = y[-1] + np.cumsum(np.full(REMAINING_DAYS, ma_7))
        
        try:
            model = ExponentialSmoothing(amj_df['Daily_Achieved'], trend='add', seasonal=None)
            fit = model.fit()
            hw_daily_pred = fit.forecast(REMAINING_DAYS)
            hw_pred = y[-1] + np.cumsum(hw_daily_pred.values)
        except:
            hw_pred = ma_pred
            
        forecast_dates = pd.date_range(start=ACTUALS_END + timedelta(days=1), end=AMJ_END, freq='D')
        
        expected_final = hw_pred[-1]
        expected_pct = expected_final / TOTAL_DB
        prob_target = max(0, min(100, (expected_final / TARGET_COUNT) * 100))
        
        # Plotly JSONs
        fig_burn = go.Figure()
        fig_burn.add_trace(go.Scatter(x=amj_df['Date'], y=amj_df['Cumulative_Achieved'], mode='lines+markers', name='Actual Achievement', line=dict(color='#f97316', width=3)))
        target_line = np.linspace(0, TARGET_COUNT, TOTAL_AMJ_DAYS)
        all_dates = pd.date_range(start=AMJ_START, end=AMJ_END, freq='D')
        fig_burn.add_trace(go.Scatter(x=all_dates, y=target_line, mode='lines', name='Linear Target', line=dict(color='#94a3b8', dash='dash')))
        fig_burn.add_trace(go.Scatter(x=forecast_dates, y=hw_pred, mode='lines', name='Expected Forecast', line=dict(color='#3b82f6', width=2, dash='dot')))
        fig_burn.add_trace(go.Scatter(x=forecast_dates, y=lr_pred, mode='lines', name='Aggressive Forecast', line=dict(color='#10b981', width=2, dash='dot')))
        fig_burn.add_hline(y=TARGET_COUNT, line_dash="solid", line_color="#0f172a", annotation_text="FINAL TARGET (8%)")
        fig_burn.update_layout(
            margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            legend=dict(orientation="h", y=-0.2)
        )
        burn_json = fig_burn.to_json()
        
        fig_gauge = go.Figure(go.Indicator(
            mode = "gauge+number+delta",
            value = current_achieved,
            title = {'text': "Target Progress"},
            delta = {'reference': TARGET_COUNT, 'increasing': {'color': "green"}},
            gauge = {
                'axis': {'range': [None, TARGET_COUNT]},
                'bar': {'color': "#f97316"},
                'steps': [
                    {'range': [0, TARGET_COUNT*0.5], 'color': "#fef2f2"},
                    {'range': [TARGET_COUNT*0.5, TARGET_COUNT*0.8], 'color': "#fff7ed"},
                    {'range': [TARGET_COUNT*0.8, TARGET_COUNT], 'color': "#f0fdf4"}
                ],
                'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': TARGET_COUNT}
            }
        ))
        fig_gauge.update_layout(margin=dict(l=20, r=20, t=30, b=20), paper_bgcolor='rgba(0,0,0,0)')
        gauge_json = fig_gauge.to_json()
        
        fig_daily = px.bar(amj_df, x='Date', y='Daily_Achieved', color_discrete_sequence=['#3b82f6'])
        fig_daily.add_hline(y=req_run_rate, line_dash="dash", line_color="red", annotation_text="Req Daily Customers")
        fig_daily.update_layout(margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        daily_json = fig_daily.to_json()
        
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Active'], mode='lines', fill='tozeroy', name='Total Active', line=dict(color='#0ea5e9')))
        fig_hist.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Repeat'], mode='lines', fill='tozeroy', name='Repeat Customers', line=dict(color='#f97316')))
        fig_hist.update_layout(margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode="x unified")
        hist_json = fig_hist.to_json()
        
        context.update({
            'total_db': TOTAL_DB,
            'target_count': TARGET_COUNT,
            'current_achieved': current_achieved,
            'remaining_target': remaining_target,
            'current_pct': current_pct * 100,
            'current_run_rate': int(current_run_rate),
            'req_run_rate': int(req_run_rate),
            'pace_status': pace_status,
            'burn_json': burn_json,
            'gauge_json': gauge_json,
            'daily_json': daily_json,
            'hist_json': hist_json,
            'prob_target': prob_target,
            'expected_final': int(expected_final),
            'expected_pct': expected_pct * 100,
            'acceleration_req': ((req_run_rate/current_run_rate)-1)*100 if current_run_rate > 0 else 0,
            'risk_level': "High" if prob_target < 85 else "Moderate" if prob_target < 95 else "Low",
            'actuals_end': ACTUALS_END.strftime('%d-%b-%Y')
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
