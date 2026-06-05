from django.urls import path
from .views import EnterpriseAIAgentView, EnterpriseAIAgentAPIView, ExportReportView, PerformanceDashboardView

urlpatterns = [
    path('chat/', EnterpriseAIAgentView.as_view(), name='ai_chat'),
    path('api/v1/chat/', EnterpriseAIAgentAPIView.as_view(), name='ai_chat_api'),
    path('api/v1/export/', ExportReportView.as_view(), name='ai_export_api'),
    path('performance/', PerformanceDashboardView.as_view(), name='ai_performance'),
]
