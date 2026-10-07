from django.urls import path
from .views import EnterpriseAIAgentView, EnterpriseAIAgentAPIView, ExportReportView, PerformanceDashboardView
from .ch_assistant_views import (
    ClickHouseAssistantPage, ClickHouseAssistantChatAPI, ClickHouseAssistantSchemaAPI,
)

urlpatterns = [
    path('chat/', EnterpriseAIAgentView.as_view(), name='ai_chat'),
    path('api/v1/chat/', EnterpriseAIAgentAPIView.as_view(), name='ai_chat_api'),
    path('api/v1/export/', ExportReportView.as_view(), name='ai_export_api'),
    path('performance/', PerformanceDashboardView.as_view(), name='ai_performance'),

    # ClickHouse Assistant — chat with the whole ClickHouse database (read-only)
    path('clickhouse/', ClickHouseAssistantPage.as_view(), name='clickhouse_assistant'),
    path('api/v1/clickhouse/chat/', ClickHouseAssistantChatAPI.as_view(), name='clickhouse_assistant_chat_api'),
    path('api/v1/clickhouse/schema/', ClickHouseAssistantSchemaAPI.as_view(), name='clickhouse_assistant_schema_api'),
]
