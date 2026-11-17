"""Views for the ClickHouse Assistant (page + JSON APIs)."""
import json
import logging
import re

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.generic import TemplateView

from .services import ch_assistant
from .services.jazz import store as jazz_store

logger = logging.getLogger(__name__)

# Restricted accounts that do not have access to Jazz AI.
RESTRICTED_USERNAMES = {"shestart"}


class _AssistantAccessMixin(LoginRequiredMixin):
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.username in RESTRICTED_USERNAMES:
            return JsonResponse({"error": "You do not have access to Jazz AI."}, status=403)
        return super().dispatch(request, *args, **kwargs)


class ClickHouseAssistantPage(_AssistantAccessMixin, TemplateView):
    template_name = "dashboard/clickhouse_assistant.html"


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None


def _storage_error(exc) -> str:
    """Friendly message when the Jazz tables are missing (migration not applied) or the DB is unreachable."""
    text = str(exc).lower()
    if "does not exist" in text or "no such table" in text or "undefinedtable" in text:
        return "Jazz AI storage is not set up yet - run: python manage.py migrate ai_agent"
    return "Could not save (database error). Please try again."


def _ndjson(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False) + "\n"


class ClickHouseAssistantChatAPI(_AssistantAccessMixin, View):
    """
    POST either:
      1. multipart/form-data: { question: str, file: UploadedFile (optional), history: JSON str, session_id: str, clear_file: bool, stream: bool }
      2. application/json: { question: str, history: list, session_id: str, clear_file: bool, stream: bool }
    → { answer, followups, results, steps, model, elapsed_ms, file_info, log_id }
    With stream=true the response is newline-delimited JSON events: {type:"status",text} ... {type:"final",data:{...}}.
    """

    def post(self, request, *args, **kwargs):
        uploaded_file = None
        question = ""
        history = []
        clear_file = False
        stream = False
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
            stream = str(request.POST.get("stream", "")).lower() in ("true", "1")
            try:
                history = json.loads(history_raw) if isinstance(history_raw, str) else []
            except Exception:
                history = []
        else:
            payload = _json_body(request)
            if payload is None:
                return JsonResponse({"error": "Invalid JSON body."}, status=400)
            question = str(payload.get("question", "")).strip()
            history = payload.get("history") or []
            client_sid = payload.get("session_id")
            if client_sid:
                session_id = client_sid
            clear_file = bool(payload.get("clear_file"))
            stream = bool(payload.get("stream"))

        if not isinstance(history, list):
            history = []

        if clear_file and session_id:
            from .services import file_analyzer
            file_analyzer.clear_cached_file_context(session_id)
            if not question and not uploaded_file:
                return JsonResponse({"status": "cleared"})

        if not question and not uploaded_file:
            return JsonResponse({"error": "Please provide a question or upload a file."}, status=400)

        limit_msg = jazz_store.check_rate_limit(request.user.id)
        if limit_msg:
            return JsonResponse({"error": limit_msg}, status=429)

        user = request.user
        had_file = uploaded_file is not None

        if stream:
            return self._stream(user, session_id, question, history, uploaded_file, had_file)

        try:
            result = ch_assistant.ask(question=question, history=history, uploaded_file=uploaded_file, session_id=session_id)
            result["log_id"] = jazz_store.log_turn(user, session_id, question, result, had_file)
            return JsonResponse(result)
        except ch_assistant.LLMError as e:
            jazz_store.log_turn(user, session_id, question, {}, had_file, error=str(e))
            return JsonResponse({"error": str(e)}, status=503)
        except Exception as e:  # noqa: BLE001
            logger.exception("ClickHouse assistant failed")
            jazz_store.log_turn(user, session_id, question, {}, had_file, error=f"{type(e).__name__}: {e}")
            return JsonResponse({"error": "Something went wrong while answering. Please try again."}, status=500)

    @staticmethod
    def _stream(user, session_id, question, history, uploaded_file, had_file):
        def gen():
            try:
                for ev in ch_assistant.ask_iter(question=question, history=history, uploaded_file=uploaded_file, session_id=session_id):
                    if ev.get("type") == "final":
                        ev["data"]["log_id"] = jazz_store.log_turn(user, session_id, question, ev["data"], had_file)
                    yield _ndjson(ev)
            except ch_assistant.LLMError as e:
                jazz_store.log_turn(user, session_id, question, {}, had_file, error=str(e))
                yield _ndjson({"type": "error", "error": str(e), "status": 503})
            except Exception as e:  # noqa: BLE001
                logger.exception("ClickHouse assistant failed (stream)")
                jazz_store.log_turn(user, session_id, question, {}, had_file, error=f"{type(e).__name__}: {e}")
                yield _ndjson({"type": "error", "error": "Something went wrong while answering. Please try again.", "status": 500})

        resp = StreamingHttpResponse(gen(), content_type="application/x-ndjson")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"   # disable nginx buffering so progress shows live
        return resp


class ClickHouseAssistantFeedbackAPI(_AssistantAccessMixin, View):
    """POST { log_id, rating: 1|-1, comment? } → stores 👍/👎 (helpful answers become golden-query candidates)."""

    def post(self, request, *args, **kwargs):
        payload = _json_body(request) or {}
        try:
            log_id = int(payload.get("log_id"))
            rating = int(payload.get("rating"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "log_id and rating are required."}, status=400)
        try:
            res = jazz_store.save_feedback(request.user, log_id, rating, str(payload.get("comment") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Jazz feedback failed")
            return JsonResponse({"error": _storage_error(exc)}, status=500)
        return JsonResponse(res, status=400 if "error" in res else 200)


class ClickHouseAssistantHistoryAPI(_AssistantAccessMixin, View):
    """GET → { history: [recent questions], saved: [starred prompts] } for the signed-in user."""

    def get(self, request, *args, **kwargs):
        return JsonResponse({"history": jazz_store.recent_history(request.user), "saved": jazz_store.list_saved(request.user)})


class ClickHouseAssistantSavedPromptAPI(_AssistantAccessMixin, View):
    """POST { prompt } → star a prompt. DELETE ?id= → remove it."""

    def post(self, request, *args, **kwargs):
        payload = _json_body(request) or {}
        try:
            res = jazz_store.add_saved(request.user, str(payload.get("prompt") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Jazz save prompt failed")
            return JsonResponse({"error": _storage_error(exc)}, status=500)
        return JsonResponse(res, status=400 if "error" in res else 200)

    def delete(self, request, *args, **kwargs):
        try:
            jazz_store.delete_saved(request.user, int(request.GET.get("id", "0")))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid id."}, status=400)
        return JsonResponse({"status": "deleted"})


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
                "name": f"{t['database']}.{t['name']}" if t["database"] not in ("default", "Loyalty_Analytics") else t["name"],
                "engine": t["engine"],
                "rows": t["rows"],
                "bytes": t["bytes"],
                "columns": [{"name": c["name"], "type": c["type"]} for c in t["columns"]],
            }
            for t in data["tables"]
        ]
        return JsonResponse({"tables": slim, "loaded_at": data["loaded_at"]})


class ClickHouseAssistantReportDownload(_AssistantAccessMixin, View):
    """GET → download a workbook built by Jazz's `build_excel_report` tool (?name= sets the file name)."""

    def get(self, request, report_id, *args, **kwargs):
        from django.http import FileResponse, Http404
        from .services.jazz import report_builder
        path = report_builder.path_for(report_id)
        if not path:
            raise Http404("Report expired or not found. Ask Jazz AI to build it again.")
        name = re.sub(r"[^A-Za-z0-9 _.-]+", "", request.GET.get("name", "")).strip() or "Jazz_Report.xlsx"
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        return FileResponse(open(path, "rb"), as_attachment=True, filename=name,
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
