from django.urls import path
from . import views

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('customers/', views.CustomerAnalyticsView.as_view(), name='customers'),
    path('rfm/', views.RFMView.as_view(), name='rfm'),
    path('cohorts/', views.CohortView.as_view(), name='cohorts'),
    path('payments/', views.PaymentView.as_view(), name='payments'),
    path('discounts/', views.DiscountView.as_view(), name='discounts'),
    path('staff/', views.StaffView.as_view(), name='staff'),
    path('branches/', views.BranchView.as_view(), name='branches'),
    path('loyalty-gap/', views.LoyaltyGapView.as_view(), name='loyalty_gap'),
    path('retail-analytics/', views.RetailAnalyticsView.as_view(), name='retail_analytics'),
    path('invalid-mobiles/', views.InvalidMobilesView.as_view(), name='invalid_mobiles'),
    path('db-manager/', views.DBManagerView.as_view(), name='db_manager'),
    path('react-dashboard/', views.ReactDashboardView.as_view(), name='react_dashboard'),
]
