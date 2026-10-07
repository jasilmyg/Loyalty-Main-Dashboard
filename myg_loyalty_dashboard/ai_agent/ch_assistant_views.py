"""Views for the ClickHouse Assistant (page + JSON APIs)."""
import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from .services import ch_assistant

logger = logging.getLogger(__name__)

# Same restricted accounts that are hidden from the "Enterprise Intelligence" menu.
RESTRICTED_USERNAMES = {"mygusers", "shestart"}


class _AssistantAccessMixin(LoginRequiredMixin):
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.username in RESTRICTED_USERNAMES:
            return JsonResponse({"error": "You do not have access to the ClickHouse Assistant."}, status=403)
        return super().dispatch(request, *args, **kwargs)


class ClickHouseAssistantPage(_AssistantAccessMixin, TemplateView):
    template_name = "dashboard/clickhouse_assistant.html"


class ClickHouseAssistantChatAPI(_AssistantAccessMixin, View):
    """POST {question, history:[{role, content}]} → {answer, results, steps, model, elapsed_ms}"""

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON body."}, status=400)
        question = str(payload.get("question", "")).strip()
        if not question:
            return JsonResponse({"error": "Question is required."}, status=400)
        history = payload.get("history") or []
        if not isinstance(history, list):
            history = []
        try:
            return JsonResponse(ch_assistant.ask(question, history))
        except ch_assistant.LLMError as e:
            return JsonResponse({"error": str(e)}, status=503)
        except Exception:  # noqa: BLE001
            logger.exception("ClickHouse assistant failed")
            return JsonResponse({"error": "Something went wrong while answering. Please try again."}, status=500)


class ClickHouseAssistantSchemaAPI(_AssistantAccessMixin, View):
    """GET → catalog of tables/columns for the sidebar explorer. ?refresh=1 forces a reload."""

    def get(self, request, *args, **kwargs):
        try:
            data = ch_assistant.get_schema(force=request.GET.get("refresh") == "1")
        except Exception as e:  # noqa: BLE001
            logger.exception("Schema load failed")
            return JsonResponse({"error": f"Could not load schema: {e}"}, status=503)
        slim = [
            {
                "name": f"{t['database']}.{t['name']}" if t["database"] != "default" else t["name"],
                "engine": t["engine"],
                "rows": t["rows"],
                "bytes": t["bytes"],
                "columns": [{"name": c["name"], "type": c["type"]} for c in t["columns"]],
            }
            for t in data["tables"]
        ]
        return JsonResponse({"tables": slim, "loaded_at": data["loaded_at"]})
