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

class DBManagerView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'dashboard/db_manager.html'
    
    def test_func(self):
        return self.request.user.is_superuser

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
