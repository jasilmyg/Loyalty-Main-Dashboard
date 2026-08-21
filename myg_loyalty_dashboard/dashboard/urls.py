from django.urls import path
from django.views.generic import RedirectView
from . import views
from . import portal_views
from .ai_chat_views import AIChatView, AIChatAPIView

urlpatterns = [
    # --- ENTERPRISE AI RETAIL INTELLIGENCE PORTAL ---
    path('executive/', portal_views.ExecutiveDashboardView.as_view(), name='executive_dashboard'),
    path('customer-intelligence-portal/', portal_views.CustomerIntelligenceView.as_view(), name='customer_intelligence'),
    path('customer-segmentation/', portal_views.CustomerSegmentationView.as_view(), name='customer_segmentation'),
    path('sales-intelligence/', portal_views.SalesIntelligenceView.as_view(), name='sales_intelligence'),
    # Sales Forecasting is already defined at views.SalesForecastingView.as_view(), keeping legacy for now or mapping it
    path('product-intelligence/', portal_views.ProductIntelligenceView.as_view(), name='product_intelligence'),
    path('recommendation-engine/', portal_views.RecommendationEngineView.as_view(), name='recommendation_engine'),
    path('inventory-intelligence/', portal_views.InventoryIntelligenceView.as_view(), name='inventory_intelligence'),
    path('promotion-intelligence/', portal_views.PromotionIntelligenceView.as_view(), name='promotion_intelligence'),
    path('branch-intelligence/', portal_views.BranchIntelligenceView.as_view(), name='branch_intelligence'),
    path('ai-insights-center/', portal_views.AIInsightsCenterView.as_view(), name='ai_insights_center'),
    path('reports-exports/', portal_views.ReportsExportsView.as_view(), name='reports_exports'),
    path('data-management/', portal_views.DataManagementView.as_view(), name='data_management'),
    path('model-management/', portal_views.ModelManagementView.as_view(), name='model_management'),
    path('settings-portal/', portal_views.SettingsPortalView.as_view(), name='settings_portal'),
    # ------------------------------------------------

    path('', views.DashboardView.as_view(), name='dashboard'),
    path('azure-analytics/', views.AzureAnalyticsDashboardView.as_view(), name='azure_analytics'),
    path('customers/', views.CustomerAnalyticsView.as_view(), name='customers'),
    path('rfm/', views.RFMView.as_view(), name='rfm'),
    path('cohorts/', views.CohortView.as_view(), name='cohorts'),
    path('payments/', views.PaymentView.as_view(), name='payments'),
    path('discounts/', views.DiscountView.as_view(), name='discounts'),
    path('staff/', views.StaffView.as_view(), name='staff'),
    path('branches/', views.BranchView.as_view(), name='branches'),
    path('loyalty-gap/', views.LoyaltyGapView.as_view(), name='loyalty_gap'),
    path('retail-analytics/', views.RetailAnalyticsView.as_view(), name='retail_analytics'),
    path('category-analysis/', views.CategoryAnalysisView.as_view(), name='category_analysis'),
    path('invalid-mobiles/', views.InvalidMobilesView.as_view(), name='invalid_mobiles'),
    path('db-manager/', views.DBManagerView.as_view(), name='db_manager'),
    path('api/v1/db-manager/refresh-mvs/', views.DBManagerRefreshMVsView.as_view(), name='db_manager_refresh_mvs'),
    path('target-executive/', views.TargetExecutiveView.as_view(), name='target_executive'),
    path('react-dashboard/', views.ReactDashboardView.as_view(), name='react_dashboard'),
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('security/', views.SecurityView.as_view(), name='security'),
    path('forecast/lstm/', views.LstmForecastView.as_view(), name='lstm_forecast'),
    path('forecast/propensity/api/', views.PropensityForecastAPIView.as_view(), name='propensity_forecast_api'),
    path('customer-intelligence/', views.CustomerPropensityView.as_view(), name='customer_propensity'),
    path('api/v1/customer-propensity/search/', views.CustomerPropensitySearchAPIView.as_view(), name='customer_propensity_search'),
    path('api/v1/customer-propensity/rebuild/', views.CustomerPropensityRebuildAPIView.as_view(), name='customer_propensity_rebuild'),
    path('report-generator/', RedirectView.as_view(url='/enterprise-dashboard/', permanent=False)),
    path('enterprise-dashboard/', views.EnterpriseDashboardView.as_view(), name='enterprise_dashboard'),
    path('api/v1/enterprise-dashboard/', views.EnterpriseDashboardAPIView.as_view(), name='enterprise_dashboard_api'),
    path('api/v1/enterprise-dashboard/export/', views.EnterpriseDashboardExportAPIView.as_view(), name='enterprise_dashboard_export'),
    path('monthly-retention/', views.MonthlyRetentionView.as_view(), name='monthly_retention'),
    path('api/v1/monthly-retention/', views.MonthlyRetentionAPIView.as_view(), name='monthly_retention_api'),
    path('campaign-analysis/', views.CampaignAnalysisView.as_view(), name='campaign_analysis'),
    path('api/v1/campaign-analysis/', views.CampaignAnalysisAPIView.as_view(), name='campaign_analysis_api'),
    path('api/v1/campaign-analysis/download-resurrected/', views.CampaignResurrectedDownloadAPIView.as_view(), name='campaign_resurrected_download'),
    path('api/v1/campaign-analysis/download-dormant/', views.CampaignDormantDownloadAPIView.as_view(), name='campaign_dormant_download'),
    path('api/v1/campaign-analysis/download-loyalty/', views.CampaignLoyaltyDownloadAPIView.as_view(), name='campaign_loyalty_download'),
    path('ai-intelligence/', views.AIIntelligenceView.as_view(), name='ai_intelligence'),
    path('api/v1/ai-intelligence/', views.AIIntelligenceAPIView.as_view(), name='ai_intelligence_api'),
    path('sales-forecasting/', views.SalesForecastingView.as_view(), name='sales_forecasting'),
    path('redemption-analysis/', views.RedemptionAnalysisView.as_view(), name='redemption_analysis'),
    path('api/v1/redemption-analysis/', views.RedemptionAnalysisAPIView.as_view(), name='redemption_analysis_api'),
    path('she-start/', views.SheStartView.as_view(), name='she_start'),
    path('api/v1/she-start/data/', views.SheStartDataAPIView.as_view(), name='she_start_data_api'),
    path('api/v1/she-start/save-score/', views.SheStartSaveScoreAPIView.as_view(), name='she_start_save_score_api'),
    path('she-start-detailed/', views.SheStartDetailedView.as_view(), name='she_start_detailed'),
    path('api/v1/she-start-detailed/data/', views.SheStartDetailedDataAPIView.as_view(), name='she_start_detailed_data_api'),
    path('api/v1/branch-customer-download/', views.BranchCustomerDownloadAPIView.as_view(), name='branch_customer_download'),
    path('api/v1/dormant-bill-range-download/', views.DormantBillRangeDownloadAPIView.as_view(), name='dormant_bill_range_download'),
    
    # Store Excel Analysis
    path('store-analysis/upload/', views.StoreAnalysisUploadView.as_view(), name='store_analysis_upload'),
    path('store-analysis/results/', views.StoreAnalysisResultsView.as_view(), name='store_analysis_results'),
    path('api/v1/store-analysis/process/', views.StoreAnalysisProcessAPIView.as_view(), name='store_analysis_process_api'),

    # AI Chat
    path('ai-chat/', AIChatView.as_view(), name='ai_chat'),
    path('ai-chat/api/', AIChatAPIView.as_view(), name='ai_chat_api'),

    # Daily New vs Repeat
    path('daily-new-repeat/', views.DailyNewRepeatView.as_view(), name='daily_new_repeat'),
    path('api/v1/daily-new-repeat/', views.DailyNewRepeatAPIView.as_view(), name='daily_new_repeat_api'),

    # Target Achievement Command Center
    path('target-command-center/', views.TargetCommandCenterView.as_view(), name='target_command_center'),
    path('api/v1/target-command-center/', views.TargetCommandCenterAPIView.as_view(), name='target_command_center_api'),

    # AI Customer Targeting Engine
    path('ai-targeting/', views.AITargetingView.as_view(), name='ai_targeting'),
    path('api/v1/ai-targeting/', views.AITargetingAPIView.as_view(), name='ai_targeting_api'),

    # MY PARF Perfume Data Download
    path('my-parf/', views.MyParfDownloadView.as_view(), name='my_parf_download'),
    path('download/my-parf/<str:data_type>/', views.MyParfDataAPIView.as_view(), name='my_parf_data_api'),
    
    # Dormant Customers Download
    path('download/dormant-customers/', views.DormantCustomersDownloadView.as_view(), name='dormant_customers_download'),

    # Product Penetration Report
    path('product-penetration/', views.ProductPenetrationView.as_view(), name='product_penetration'),
    path('api/v1/product-penetration/', views.ProductPenetrationAPIView.as_view(), name='product_penetration_api'),

    # Market Basket Analysis / Recommendation System
    path('market-basket/', views.MarketBasketView.as_view(), name='market_basket'),
    path('api/v1/market-basket/', views.MarketBasketAPIView.as_view(), name='market_basket_api'),
]
