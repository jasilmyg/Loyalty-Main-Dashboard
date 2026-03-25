from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin

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
