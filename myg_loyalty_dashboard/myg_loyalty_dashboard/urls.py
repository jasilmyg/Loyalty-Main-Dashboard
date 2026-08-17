"""
URL configuration for myg_loyalty_dashboard project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('api/v1/', include('analytics.urls')),
    path('ai/', include('ai_agent.urls')),
    path('', include('dashboard.urls')),
]

# Trigger reload

# Reload for LSTM fix

# Force reload 2
# Force reload for Sales Forecasting
