from django.urls import path
from . import views

urlpatterns = [
    path('sales-overview/', views.SalesOverviewAPI.as_view(), name='api-sales-overview'),
    path('customer-analytics/', views.CustomerAnalyticsAPI.as_view(), name='api-customer-analytics'),
    path('customer-frequency/', views.FrequencyDistributionAPI.as_view(), name='api-customer-frequency'),
    path('rfm-segments/', views.RFMAnalysisAPI.as_view(), name='api-rfm-segments'),
    path('monetary-quintiles/', views.MonetaryQuintilesAPI.as_view(), name='api-monetary-quintiles'),
    path('cohorts/', views.CohortRetentionAPI.as_view(), name='api-cohorts'),
    path('yearly-cohorts/', views.YearlyCohortAPI.as_view(), name='api-yearly-cohorts'),
    path('payment-analytics/', views.PaymentAnalyticsAPI.as_view(), name='api-payment-analytics'),
    path('discount-analysis/', views.DiscountAnalysisAPI.as_view(), name='api-discount-analysis'),
    path('staff-performance/', views.StaffPerformanceAPI.as_view(), name='api-staff-performance'),
    path('branch-performance/', views.BranchPerformanceAPI.as_view(), name='api-branch-performance'),
    path('loyalty-overview/', views.LoyaltyOverviewAPI.as_view(), name='api-loyalty-overview'),
    path('gap-segments/', views.GapAnalysisAPI.as_view(), name='api-gap-segments'),
    path('loyalty-segmentation/', views.LoyaltySegmentationAPI.as_view(), name='api-loyalty-segmentation'),
    path('action-engine/', views.ActionEngineAPI.as_view(), name='api-action-engine'),
    path('branches-list/', views.BranchesAPI.as_view(), name='api-branches-list'),
    path('business-insights/', views.BusinessInsightsAPI.as_view(), name='api-business-insights'),
    path('retail-loyalty-report/', views.RetailLoyaltyReportAPI.as_view(), name='api-retail-loyalty-report'),
    path('retail-loyalty-advanced/', views.RetailLoyaltyAdvancedReportAPI.as_view(), name='api-retail-loyalty-advanced'),
    path('fy-loyalty-report/', views.FYLoyaltyReportAPI.as_view(), name='api-fy-loyalty-report'),
    path('fy-sales-report/', views.FYSalesReportAPI.as_view(), name='api-fy-sales-report'),
    path('invalid-mobiles-list/', views.InvalidMobilesAPI.as_view(), name='api-invalid-mobiles'),
    path('db-manager/', views.DBManagerAPI.as_view(), name='api-db-manager'),

    # Old DRF-wrapped route kept for backward compat
    path('export/<str:module>/', views.ExportAPIView.as_view(), name='api-export'),
    # New plain Django route — reliable binary file downloads
    path('download/<str:module>/', views.export_view, name='api-download'),
]
