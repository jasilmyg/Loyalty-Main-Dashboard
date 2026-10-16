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
RESTRICTED_USERNAMES = {"mygusers", "myguser", "shestart"}


class _AssistantAccessMixin(LoginRequiredMixin):
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.username in RESTRICTED_USERNAMES:
            return JsonResponse({"error": "You do not have access to Jazz AI."}, status=403)
        return super().dispatch(request, *args, **kwargs)


class ClickHouseAssistantPage(_AssistantAccessMixin, TemplateView):
    template_name = "dashboard/clickhouse_assistant.html"


class ClickHouseAssistantChatAPI(_AssistantAccessMixin, View):
    """
    POST either:
      1. multipart/form-data: { question: str, file: UploadedFile (optional), history: JSON str, session_id: str, clear_file: bool }
      2. application/json: { question: str, history: list, session_id: str, clear_file: bool }
    → { answer, results, steps, model, elapsed_ms, file_info }
    """

    def post(self, request, *args, **kwargs):
        uploaded_file = None
        question = ""
        history = []
        clear_file = False
        session_id = request.session.session_key or ""
        if not session_id:
            try:
                request.session.save()
                session_id = request.session.session_key or ""
            except Exception:
                pass

        if request.content_type and "multipart/form-data" in request.content_type:
            uploaded_file = request.FILES.get("file")
            question = str(request.POST.get("question", "")).strip()
            history_raw = request.POST.get("history", "[]")
            client_sid = request.POST.get("session_id")
            if client_sid:
                session_id = client_sid
            clear_file = str(request.POST.get("clear_file", "")).lower() in ("true", "1")
            try:
                history = json.loads(history_raw) if isinstance(history_raw, str) else []
            except Exception:
                history = []
        else:
            try:
                payload = json.loads(request.body or b"{}")
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON body."}, status=400)
            question = str(payload.get("question", "")).strip()
            history = payload.get("history") or []
            client_sid = payload.get("session_id")
            if client_sid:
                session_id = client_sid
            clear_file = bool(payload.get("clear_file"))

        if not isinstance(history, list):
            history = []

        if clear_file and session_id:
            from .services import file_analyzer
            file_analyzer.clear_cached_file_context(session_id)
            if not question and not uploaded_file:
                return JsonResponse({"status": "cleared"})

        if not question and not uploaded_file:
            return JsonResponse({"error": "Please provide a question or upload a file."}, status=400)

        try:
            return JsonResponse(
                ch_assistant.ask(
                    question=question,
                    history=history,
                    uploaded_file=uploaded_file,
                    session_id=session_id,
                )
            )
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
