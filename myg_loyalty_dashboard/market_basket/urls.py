from django.urls import path
from . import views

urlpatterns = [
    # Dashboard page
    path('', views.MarketBasketDashboardView.as_view(), name='market_basket_dashboard'),

    # API endpoints
    path('api/kpis/',                  views.MarketBasketKPIsAPIView.as_view(),           name='mb_api_kpis'),
    path('api/associations/',          views.MarketBasketAssociationsAPIView.as_view(),   name='mb_api_associations'),
    path('api/opportunities/',         views.MarketBasketOpportunitiesAPIView.as_view(),  name='mb_api_opportunities'),
    path('api/network/',               views.MarketBasketNetworkAPIView.as_view(),        name='mb_api_network'),
    path('api/category-matrix/',       views.MarketBasketCategoryMatrixAPIView.as_view(), name='mb_api_category_matrix'),
    path('api/branch-performance/',    views.MarketBasketBranchPerfAPIView.as_view(),     name='mb_api_branch_perf'),
    path('api/salesperson-performance/', views.MarketBasketSalespersonPerfAPIView.as_view(), name='mb_api_staff_perf'),
    path('api/customer-recommendations/', views.MarketBasketCustomerRecsAPIView.as_view(), name='mb_api_customer_recs'),
    path('api/product-recommendations/', views.MarketBasketProductRecsAPIView.as_view(),  name='mb_api_product_recs'),
    path('api/sequential/',            views.MarketBasketSequentialAPIView.as_view(),     name='mb_api_sequential'),
    path('api/ai-insights/',           views.MarketBasketAIInsightsAPIView.as_view(),     name='mb_api_insights'),
    path('api/precompute/',            views.MarketBasketPrecomputeAPIView.as_view(),     name='mb_api_precompute'),
]
