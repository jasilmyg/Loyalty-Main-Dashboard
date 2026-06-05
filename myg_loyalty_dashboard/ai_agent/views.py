from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .agents.sql_agent import SQLAgent
from .agents.analytics_agent import AnalyticsAgent
from .agents.visualization_agent import VisualizationAgent
from .agents.forecast_agent import ForecastAgent
from .agents.rfm_agent import RFMAgent
from .agents.retention_agent import RetentionAgent
from .agents.recommendation_agent import RecommendationAgent
from .services.security_service import SecurityService
from .services.export_service import ExportService

class EnterpriseAIAgentView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/enterprise_ai_agent.html'
    login_url = '/accounts/login/'

class EnterpriseAIAgentAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import time
        import hashlib
        from django.core.cache import cache
        
        start_time = time.time()
        
        prompt = request.data.get('prompt', '')
        conversation_id = request.data.get('conversation_id', None)
        prompt_lower = prompt.lower()
        
        # Phase 15: Performance Layer - Cache Check
        cache_key = f"ai_response_{request.user.id}_{hashlib.md5(prompt_lower.encode('utf-8')).hexdigest()}"
        cached_response = cache.get(cache_key)
        
        if cached_response:
            from .models import AIAuditLog
            execution_time = int((time.time() - start_time) * 1000)
            AIAuditLog.objects.create(
                user=request.user,
                prompt=prompt,
                matched_agent="CacheLayer (Redis/Mem)",
                generated_sql="[CACHED HIT]",
                execution_time_ms=execution_time
            )
            
            # Ensure conversation ID is updated if they are continuing a thread
            if conversation_id:
                cached_response["conversation_id"] = conversation_id
                
            return Response(cached_response)
        
        matched_agent_name = "Unknown"
        generated_sql_str = ""
        
        # Phase 5: Role Security Context
        user_context = SecurityService.get_user_context(request.user)
        
        # Phase 7: Load Conversation Memory
        from .memory.conversation_memory import ConversationMemory
        conversation = ConversationMemory.get_or_create_conversation(request.user, conversation_id)
        
        from .agents.router import RouterAgent
        
        # ── The Enterprise 6-Layer Pipeline ──
        
        generated_sql_str = ""
        try:
            # 1. Visualization Pipeline
            if any(word in prompt_lower for word in ["chart", "graph", "plot", "trend", "distribution"]):
                matched_agent_name = "VisualizationAgent"
                from .agents.visualization_agent import VisualizationAgent
                from .agents.sql_agent import SQLAgent
                
                v_agent = VisualizationAgent()
                sql_agent = SQLAgent()
                
                chart_json = v_agent.generate_dynamic_chart(prompt, sql_agent, user_context)
                if "error" in chart_json:
                    response_data = {
                        "message": f"❌ **Visualization Error:** {chart_json['error']}",
                        "charts": [], "kpis": []
                    }
                else:
                    response_data = {
                        "message": "📊 **Visualization Agent:** I've dynamically generated the requested chart using real-time data.",
                        "charts": [chart_json],
                        "kpis": []
                    }
                    
            elif any(word in prompt_lower for word in ["forecast", "predict", "expected", "will we achieve"]):
                matched_agent_name = "ForecastAgent (Async)"
                from .tasks import generate_forecast_report_task
                generate_forecast_report_task.delay(request.user.id, prompt, conversation_id)
                
                # If running locally (EAGER = True), the task is already finished. Check the cache immediately.
                result_key = f"task_result_forecast_{request.user.id}_{conversation_id}"
                eager_result = cache.get(result_key)
                
                if eager_result:
                    response_data = eager_result
                else:
                    response_data = {
                        "message": "⏳ **Forecasting Engine:** I have started generating your complex forecast in the background. This will take ~10 seconds. The UI will stream updates...",
                        "charts": [], "kpis": [],
                        "task_id": conversation_id,
                        "is_async": True
                    }
                
            # 3. Standard Text Analysis Pipeline (Schema -> SQL -> DB -> Analyst)
            else:
                matched_agent_name = "Enterprise AI Pipeline"
                from .agents.sql_agent import SQLAgent
                from .agents.analyst_agent import AnalystAgent
                
                sql_agent = SQLAgent()
                analyst_agent = AnalystAgent()
                
                # Layer 4 (SQL Generation via Schema Catalog)
                generated_sql, error_msg = sql_agent.generate_query(prompt, user_context, model_name="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
                generated_sql_str = generated_sql or ""
                
                if error_msg:
                    response_data = {
                        "message": f"❌ **SQL Generation Error:** {error_msg}",
                        "charts": [], "kpis": []
                    }
                else:
                    # Layer 5: PostgreSQL Execution
                    results = sql_agent.execute_query(generated_sql)
                    
                    if not results:
                        response_data = {
                            "message": f"✅ **SQL Executed Successfully**\n```sql\n{generated_sql}\n```\n\nNo data found for this request.",
                            "charts": [], "kpis": []
                        }
                    elif "error" in results[0]:
                        response_data = {
                            "message": f"❌ **Execution Error:**\n```sql\n{generated_sql}\n```\n\n{results[0]['error']}",
                            "charts": [], "kpis": []
                        }
                    else:
                        # Layer 6: AI Analyst (Kimi)
                        insight_text = analyst_agent.analyze_results(prompt, generated_sql, results)
                        
                        response_data = {
                            "message": f"🧠 **AI Analyst Insight:**\n\n{insight_text}\n\n---\n*Execution Time: {time.time() - start_time:.2f}s*",
                            "charts": [],
                            "kpis": []
                        }
        except Exception as e:
            response_data = {
                "message": f"❌ **System Error:** {str(e)}",
                "charts": [], "kpis": []
            }


        # Cache the response for future identical queries
        cache.set(cache_key, response_data, timeout=300) # Cache for 5 minutes
            
        # Extract text message to save
        ai_response_text = response_data.get("message", "")
        
        # Phase 7: Save to Database Memory
        ConversationMemory.save_interaction(conversation, prompt, ai_response_text)
        
        # Phase 14: Save Audit Log
        from .models import AIAuditLog
        execution_time = int((time.time() - start_time) * 1000)
        AIAuditLog.objects.create(
            user=request.user,
            prompt=prompt,
            matched_agent=matched_agent_name,
            generated_sql=generated_sql_str,
            execution_time_ms=execution_time
        )
        
        # Return conversation_id to maintain thread
        response_data["conversation_id"] = conversation.id
        
        # Phase 15: Store Response in Cache for 5 minutes
        cache.set(cache_key, response_data, timeout=300)
            
        return Response(response_data)

class ExportReportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        content = request.data.get('content', '')
        export_type = request.data.get('type', 'pdf')
        
        if export_type == 'excel':
            # Mocking data conversion for Excel
            mock_data = [{"Metric": "Export", "Status": "Success", "Note": "This is a mock CSV export from the AI."}]
            csv_data = ExportService.export_to_csv(mock_data)
            
            response = HttpResponse(csv_data, content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="ai_report.csv"'
            return response
            
        else: # Default to PDF/Text
            text_data = ExportService.export_to_text(content)
            response = HttpResponse(text_data, content_type='text/plain')
            response['Content-Disposition'] = 'attachment; filename="ai_report.txt"'
            return response

class PerformanceDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/ai_performance.html'
    login_url = '/accounts/login/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import AIAuditLog
        from django.db.models import Avg, Max
        
        logs = AIAuditLog.objects.all().order_by('-created_at')[:50]
        stats = AIAuditLog.objects.aggregate(
            avg_time=Avg('execution_time_ms'),
            max_time=Max('execution_time_ms')
        )
        
        context['logs'] = logs
        context['avg_time_sec'] = (stats['avg_time'] or 0) / 1000.0
        context['max_time_sec'] = (stats['max_time'] or 0) / 1000.0
        return context
