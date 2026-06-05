import time
from celery import shared_task
from django.core.cache import cache

@shared_task
def generate_forecast_report_task(user_id: int, prompt: str, conversation_id: str):
    """
    Background Celery task for running heavy AI forecasting models.
    Saves the generated report to Redis cache for the frontend to poll and retrieve.
    """
    from .agents.forecast_agent import ForecastAgent
    
    # 1. Update status to 'processing'
    status_key = f"task_status_forecast_{user_id}_{conversation_id}"
    cache.set(status_key, "generating_report", timeout=300)
    
    # 2. Run the heavy model
    agent = ForecastAgent()
    report_md = agent.generate_forecast(prompt)
    
    # 3. Store the result
    result_key = f"task_result_forecast_{user_id}_{conversation_id}"
    response_data = {
        "message": f"🔮 **Forecasting Engine (Async):**\n\n{report_md}",
        "charts": [], "kpis": []
    }
    
    cache.set(result_key, response_data, timeout=3600) # Cache result for 1 hour
    cache.set(status_key, "completed", timeout=300)
    
    return True
