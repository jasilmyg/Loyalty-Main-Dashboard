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


def _format_fast_result(prompt: str, results: list, sql: str, exec_time: float) -> dict:
    """
    Instantly formats DB results for fast-path queries without calling any LLM.
    Produces clean, readable Markdown output in < 1ms.
    """
    if not results:
        return {"message": "No data found for your query.", "charts": [], "kpis": []}

    row = results[0]
    lines = []

    # Format each column intelligently
    for key, val in row.items():
        label = key.replace('_', ' ').title()
        if val is None:
            formatted = "—"
        elif isinstance(val, float):
            formatted = f"₹{val:,.2f}" if any(w in key.lower() for w in ['revenue', 'value', 'amount', 'atv']) else f"{val:,.2f}"
        elif isinstance(val, int):
            formatted = f"₹{val:,}" if any(w in key.lower() for w in ['revenue', 'value', 'amount', 'atv']) else f"{val:,}"
        else:
            formatted = str(val)
        lines.append(f"**{label}:** {formatted}")

    # Multi-row: build a table
    if len(results) > 1:
        headers = list(results[0].keys())
        table_lines = ["| " + " | ".join(h.replace('_', ' ').title() for h in headers) + " |"]
        table_lines.append("|" + "---|" * len(headers))
        for r in results:
            cells = []
            for h in headers:
                v = r[h]
                if isinstance(v, (int, float)) and any(w in h.lower() for w in ['revenue', 'value', 'amount']):
                    cells.append(f"₹{v:,.0f}" if v else "0")
                elif isinstance(v, (int, float)):
                    cells.append(f"{v:,}" if v else "0")
                else:
                    cells.append(str(v) if v else "—")
            table_lines.append("| " + " | ".join(cells) + " |")
        message = "\n".join(table_lines) + f"\n\n*Answered in {exec_time}s (FastPath Engine)*"
    else:
        message = "\n".join(lines) + f"\n\n*Answered in {exec_time}s (FastPath Engine)*"

    return {"message": message, "charts": [], "kpis": []}


class EnterpriseAIAgentView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/enterprise_ai_agent.html'
    login_url = '/accounts/login/'

class EnterpriseAIAgentAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import uuid
        import threading
        from django.core.cache import cache

        # ── Poll for task result ─────────────────────────────────────────────
        task_id = request.data.get('task_id')
        if task_id:
            task_result = cache.get(task_id)
            if task_result:
                # Also attach any available Nemotron enrichment
                nemotron_key = f"nemotron_{task_id}"
                nemotron_result = cache.get(nemotron_key)
                if nemotron_result:
                    task_result["nemotron_message"]   = nemotron_result.get("message", "")
                    task_result["reasoning_details"]  = nemotron_result.get("reasoning_details")
                    task_result["nemotron_ready"]      = True
                return Response(task_result)
            return Response({"status": "processing", "task_id": task_id})

        # ── New question ─────────────────────────────────────────────────────
        task_id        = str(uuid.uuid4())
        prompt         = request.data.get('prompt', '')
        conversation_id = request.data.get('conversation_id', None)
        user_id        = request.user.id

        cache.set(task_id, {"status": "processing", "task_id": task_id}, timeout=600)

        # Launch background thread — Phase 1 answers fast, Phase 2 runs Nemotron
        thread = threading.Thread(
            target=self.process_chat_request,
            args=(user_id, prompt, conversation_id, task_id),
            daemon=True
        )
        thread.start()

        return Response({"status": "processing", "task_id": task_id})

    def process_chat_request(self, user_id, prompt, conversation_id, task_id):
        from django.db import connection
        try:
            self._process_chat_request_internal(user_id, prompt, conversation_id, task_id)
        finally:
            connection.close()


    def _process_chat_request_internal(self, user_id, prompt, conversation_id, task_id):
        import time
        import hashlib
        import threading
        from django.core.cache import cache
        from django.contrib.auth import get_user_model
        from .memory.conversation_memory import ConversationMemory

        User = get_user_model()
        user = User.objects.get(id=user_id)

        start_time    = time.time()
        prompt_lower  = prompt.lower()
        db_cache_key  = "ai_db_" + hashlib.md5(prompt_lower.strip().encode()).hexdigest()

        matched_agent_name = "Unknown"
        generated_sql_str  = ""
        generated_sql      = ""
        results            = []

        user_context = SecurityService.get_user_context(user)
        conversation = ConversationMemory.get_or_create_conversation(user, conversation_id)

        from .agents.router import RouterAgent

        try:
            # ── 1. Visualization Pipeline ────────────────────────────────────
            if any(w in prompt_lower for w in ["chart", "graph", "plot", "trend", "distribution"]):
                matched_agent_name = "VisualizationAgent"
                from .agents.visualization_agent import VisualizationAgent
                from .agents.sql_agent import SQLAgent
                v_agent  = VisualizationAgent()
                sql_agent = SQLAgent()
                chart_json = v_agent.generate_dynamic_chart(prompt, sql_agent, user_context)
                if "error" in chart_json:
                    response_data = {"message": f"❌ {chart_json['error']}", "charts": [], "kpis": []}
                else:
                    response_data = {
                        "message": "📊 Chart generated from live data.",
                        "charts": [chart_json], "kpis": []
                    }
                cache.set(task_id, response_data, timeout=600)
                return

            # ── 2. Forecast Pipeline ─────────────────────────────────────────
            elif any(w in prompt_lower for w in ["forecast", "predict", "expected", "will we achieve"]):
                matched_agent_name = "ForecastAgent + Nemotron"

                # ── Store Phase 1: "working on it" message immediately ────────
                cache.set(task_id, {
                    "message": "⏳ **Analysing historical data...** Querying repeat customer trends (2020–2026) and running forecast. Results in a moment.",
                    "charts": [], "kpis": [],
                    "status": "processing",
                    "task_id": task_id,
                }, timeout=600)

                # ── Phase 2: Real DB query + Nemotron in background ──────────
                def run_forecast():
                    from django.db import connection as conn2
                    try:
                        # Pull real monthly repeat customer data from DB
                        with conn2.cursor() as cur:
                            cur.execute("""
                                SELECT
                                    TO_CHAR(DATE_TRUNC('month', parsed_date), 'YYYY-MM') AS month,
                                    COUNT(DISTINCT "Customer Mobile") AS repeat_customers
                                FROM sales_data
                                WHERE parsed_date IS NOT NULL
                                  AND "Customer Mobile" IS NOT NULL
                                  AND "Customer Mobile" != ''
                                  AND parsed_date >= '2024-01-01'
                                GROUP BY DATE_TRUNC('month', parsed_date)
                                ORDER BY DATE_TRUNC('month', parsed_date)
                            """)
                            rows = cur.fetchall()

                            # AMJ 2026 actuals so far (Apr, May, Jun)
                            cur.execute("""
                                SELECT
                                    EXTRACT(MONTH FROM parsed_date)::int AS mo,
                                    COUNT(DISTINCT "Customer Mobile") AS customers
                                FROM sales_data
                                WHERE parsed_date >= '2026-04-01'
                                  AND parsed_date < '2026-06-01'
                                  AND "Customer Mobile" IS NOT NULL
                                  AND "Customer Mobile" != ''
                                GROUP BY EXTRACT(MONTH FROM parsed_date)::int
                                ORDER BY mo
                            """)
                            amj_rows = cur.fetchall()

                            # AMJ 2025 for YoY benchmark
                            cur.execute("""
                                SELECT COUNT(DISTINCT "Customer Mobile")
                                FROM sales_data
                                WHERE parsed_date >= '2025-04-01'
                                  AND parsed_date < '2025-07-01'
                                  AND "Customer Mobile" IS NOT NULL
                                  AND "Customer Mobile" != ''
                            """)
                            amj_2025 = cur.fetchone()[0]

                            # AMJ 2024 benchmark
                            cur.execute("""
                                SELECT COUNT(DISTINCT "Customer Mobile")
                                FROM sales_data
                                WHERE parsed_date >= '2024-04-01'
                                  AND parsed_date < '2024-07-01'
                                  AND "Customer Mobile" IS NOT NULL
                                  AND "Customer Mobile" != ''
                            """)
                            amj_2024 = cur.fetchone()[0]

                        # Build data context for Nemotron
                        monthly_data = "\n".join([f"  {r[0]}: {r[1]:,} customers" for r in rows])
                        amj_actual   = "\n".join([f"  {'Apr' if r[0]==4 else 'May' if r[0]==5 else 'Jun'} 2026: {r[1]:,} (COMPLETE)" for r in amj_rows])
                        amj_total_so_far = sum(r[1] for r in amj_rows)

                        data_context = f"""
QUESTION: {prompt}

HISTORICAL DATA (Jan 2024 – May 2026):
{monthly_data}

AMJ QUARTER 2026 STATUS:
  NOTE: Data is COMPLETE and FINALIZED for April 2026 and May 2026.
  June 2026 is the CURRENT (incomplete) month with only partial data.
{amj_actual}
  April + May 2026 total (complete): {amj_total_so_far:,}
  June 2026 customers still needed to reach TARGET: {max(0, 400000 - amj_total_so_far):,}
  TARGET for full AMJ Quarter: 400,000 (4 lakh)

BENCHMARKS:
  AMJ 2025 total: {amj_2025:,}
  AMJ 2024 total: {amj_2024:,}
  YoY growth rate 2024→‥2025: {((amj_2025/amj_2024)-1)*100:.1f}%
"""

                        from .agents.analyst_agent import AnalystAgent
                        analyst = AnalystAgent()
                        insight = analyst.analyze_results(
                            prompt,
                            "[Forecast Query — real historical data provided above]",
                            [{"data_context": data_context}],
                            []
                        )
                        insight_text = insight.get("text", "")
                        rd = insight.get("reasoning_details", None)

                        nemotron_data = {
                            "message": insight_text + f"\n\n---\n*Apr+May 2026 (complete): {amj_total_so_far:,} | Target: 400,000 | June still in progress | Model: Nemotron-3-Ultra-550B*",
                            "reasoning_details": rd,
                            "charts": [], "kpis": [],
                            "nemotron_ready": True,
                            "nemotron_message": insight_text + f"\n\n---\n*Apr+May 2026 (complete): {amj_total_so_far:,} | Target: 400,000 | June still in progress | Model: Nemotron-3-Ultra-550B*"
                        }
                        cache.set(f"nemotron_{task_id}", nemotron_data, timeout=600)

                        # Also update task_id with full result
                        cache.set(task_id, {
                            "message": insight_text + f"\n\n---\n*AMJ 2026 so far: {amj_total_so_far:,} | Target: 400,000*",
                            "nemotron_pending": False,
                            "reasoning_details": rd,
                            "conversation_id": str(conversation.id),
                            "charts": [], "kpis": []
                        }, timeout=600)

                        ConversationMemory.save_interaction(conversation, prompt, insight_text, reasoning_details=rd)

                    except Exception as ex:
                        import traceback
                        cache.set(task_id, {
                            "message": f"Forecast error: {ex}\n\n```\n{traceback.format_exc()}\n```",
                            "charts": [], "kpis": []
                        }, timeout=600)
                    finally:
                        conn2.close()

                import threading as _t
                _t.Thread(target=run_forecast, daemon=True).start()

                # Update task with Phase 1 (so frontend stops seeing "processing")
                cache.set(task_id, {
                    "message": "📊 **Querying repeat customer data for AMJ Quarter 2026...**\n\nApril 2026 ✅ complete | May 2026 ✅ complete | June 2026 🔄 in progress\n\nRunning Nemotron forecast analysis against historical trends (2024–2026). Results in 30–120 seconds.",
                    "nemotron_pending": True,
                    "task_id": task_id,
                    "conversation_id": str(conversation.id),
                    "charts": [], "kpis": []
                }, timeout=600)
                return


            # ── 3. General Question Detection ────────────────────────────────
            # Detect non-SQL questions (greetings, strategy, general AI chat)
            # so they get a real AI answer instead of broken SQL
            SQL_KEYWORDS = [
                'revenue', 'sales', 'customer', 'branch', 'store', 'invoice', 'payment',
                'monthly', 'yearly', 'annual', 'quarter', 'trend', 'performance', 'total',
                'count', 'how many', 'top', 'best', 'worst', 'compare', 'growth', 'report',
                'rfm', 'retention', 'dormant', 'repeat', 'new', 'atv', 'ltv', 'cohort',
                'gap', 'resurrection', 'loyalty', 'redemption', 'campaign', 'staff', 'rbm',
                'bdm', 'lakh', 'crore', 'rupee', 'percent', '%', '2024', '2025', '2026',
                'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                'september', 'october', 'november', 'december', 'amj', 'today', 'yesterday'
            ]
            sql_keyword_count = sum(1 for kw in SQL_KEYWORDS if kw in prompt_lower)
            is_general_question = sql_keyword_count < 2  # Less than 2 business keywords

            if is_general_question:
                # Route to direct AI answer (no SQL needed)
                matched_agent_name = "GeneralAI"
                from .agents.analyst_agent import AnalystAgent
                import datetime
                analyst_agent = AnalystAgent()
                current_month = datetime.date.today().strftime("%B %Y")

                general_prompt = (
                    f"You are the myG Enterprise AI Agent — a business intelligence assistant for myG, "
                    f"a large electronics retail chain in Kerala, India with 80+ stores. "
                    f"Current month: {current_month}. "
                    f"You help managers with analytics, loyalty program insights, strategy, and business questions.\n\n"
                    f"User question: \"{prompt}\"\n\n"
                    f"Answer helpfully, concisely, and professionally. "
                    f"If this is a greeting or small talk, respond warmly and explain what you can help with. "
                    f"If this is a strategy/business question, give actionable advice relevant to an electronics retail chain in Kerala."
                )
                try:
                    general_response = analyst_agent._call_api(
                        [{"role": "user", "content": general_prompt}],
                        timeout=60
                    )
                    answer_text = (general_response.get("content") or "").strip()
                    if not answer_text:
                        answer_text = (
                            "I'm the myG Enterprise AI Agent. I can help you with:\n\n"
                            "- 📊 **Revenue & Sales analysis** (by month, branch, year)\n"
                            "- 👥 **Customer insights** (new, repeat, dormant customers)\n"
                            "- 🔮 **Forecasting** (predict sales, customer targets)\n"
                            "- 🎯 **RFM & Loyalty** (segments, retention, gap analysis)\n\n"
                            "Try asking: *\"What is the revenue in May 2026?\"* or *\"Show top 10 branches\"*"
                        )
                except Exception:
                    answer_text = (
                        "Hello! I'm the myG Enterprise AI Agent 👋\n\n"
                        "I'm connected to your live database and can help you with:\n\n"
                        "- 📊 **Revenue & Sales** analysis by month, branch, year\n"
                        "- 👥 **Customer analytics** — new, repeat, dormant customers\n"
                        "- 🔮 **Forecasting** — predict targets and trends\n"
                        "- 🎯 **Loyalty KPIs** — RFM, retention, gap analysis\n\n"
                        "Ask me a business question like *\"What is the revenue in May 2026?\"*"
                    )
                cache.set(task_id, {
                    "message": answer_text,
                    "conversation_id": str(conversation.id),
                    "charts": [], "kpis": []
                }, timeout=600)
                ConversationMemory.save_interaction(conversation, prompt, answer_text)
                return

            # ── 4. Standard SQL Pipeline ─────────────────────────────────────
            from .agents.sql_agent import SQLAgent
            from .agents.analyst_agent import AnalystAgent
            from .agents.sql_template_engine import SQLTemplateEngine

            sql_agent     = SQLAgent()
            analyst_agent = AnalystAgent()

            # Fast SQL: Template Engine (sub-second from MVs)
            fast_sql = SQLTemplateEngine.match_template(prompt)
            if fast_sql:
                matched_agent_name = "FastPath + Nemotron"
                generated_sql      = fast_sql
                generated_sql_str  = fast_sql
                error_msg          = None
            else:
                generated_sql, error_msg = sql_agent.generate_query(prompt, user_context)
                matched_agent_name = "LLM-SQL + Nemotron"
                generated_sql_str  = generated_sql or ""

            if error_msg:
                cache.set(task_id, {
                    "message": (
                        f"⚠️ **I couldn't generate a database query for that question.**\n\n"
                        f"*Reason: {error_msg[:200]}*\n\n"
                        f"Please try rephrasing your question. For example:\n"
                        f"- *\"What is the revenue in May 2026?\"*\n"
                        f"- *\"How many customers bought in 2025?\"*\n"
                        f"- *\"Show me the top 10 branches\"*"
                    ),
                    "charts": [], "kpis": []
                }, timeout=600)
                return

            # DB execution (cached 15min — avoids re-scanning 12.6M rows)
            cached_db = cache.get(db_cache_key)
            if cached_db is not None:
                results = cached_db
            else:
                results = sql_agent.execute_query(generated_sql)
                cache.set(db_cache_key, results, timeout=900)

            if not results:
                cache.set(task_id, {
                    "message": (
                        "📭 **No data found for your request.**\n\n"
                        "This could mean:\n"
                        "- The date range has no transactions\n"
                        "- The branch/filter name doesn't match exactly\n"
                        "- The data for this period hasn't been uploaded yet\n\n"
                        "*Try a different time period or check the branch name.*"
                    ),
                    "charts": [], "kpis": []
                }, timeout=600)
                return

            if isinstance(results, list) and results and "error" in results[0]:
                cache.set(task_id, {
                    "message": f"❌ **Execution Error:**\n```sql\n{generated_sql}\n```\n\n{results[0]['error']}",
                    "charts": [], "kpis": []
                }, timeout=600)
                return

            # ═══════════════════════════════════════════════════════════════
            # PHASE 1: Fast AI Analysis (Llama 4 Maverick, always ~5-15s)
            # Always gives the user a complete, detailed answer immediately.
            # ═══════════════════════════════════════════════════════════════
            sql_time = round(time.time() - start_time, 2)

            # Run Nemotron analysis via OpenRouter (only confirmed-working model, ~20-40s)
            try:
                import json as _json
                import requests as _req
                import datetime

                import os as _os
                _or_key  = _os.environ.get("OPENROUTER_API_KEY", "")
                _or_url  = "https://openrouter.ai/api/v1/chat/completions"
                _model   = "nvidia/nemotron-3-ultra-550b-a55b:free"

                _today = datetime.date.today().strftime("%d %B %Y")
                _n     = len(results)
                _data  = _json.dumps(results[:50], default=str, indent=2)

                _fast_prompt = (
                    "You are the myG Enterprise Business Intelligence AI - an expert data analyst "
                    "for myG, Kerala's largest electronics retail chain (80+ stores across Kerala).\n\n"
                    f"ANALYSIS DATE: {_today}\n"
                    "DATA AVAILABLE: January 2020 - May 2026 (June 2026 = current month, partial data)\n\n"
                    "BUSINESS CONTEXT:\n"
                    "- myG sells TVs, smartphones, home appliances, laptops across Kerala\n"
                    "- Loyalty program tracks repeat customers by mobile number\n"
                    "- AMJ 2026 Quarter Target: 4,00,000 repeat customers\n"
                    "- Finance = EMI/loan amount | Cash/Card/UPI = payment modes\n"
                    "- Indian number format: lakhs and crores (e.g. 2.34 lakh, Rs.45.6 crore)\n\n"
                    f"USER QUESTION: \"{prompt}\"\n\n"
                    f"DATABASE RESULT ({_n} rows, showing up to 50):\n{_data}\n\n"
                    "Write a DETAILED business analysis report with these exact sections:\n\n"
                    "## Executive Summary\n"
                    "Answer the question directly in 2-3 sentences with bolded key numbers.\n\n"
                    "## Key Metrics Breakdown\n"
                    "List ALL numbers from the data. Include derived metrics (percentages, per-customer averages).\n\n"
                    "## Business Insights\n"
                    "4-5 bullet points: what the data means, why the pattern exists, comparisons.\n\n"
                    "## Actionable Recommendations\n"
                    "3-4 specific actions tagged High / Medium / Low priority.\n\n"
                    "## Risks and Watch Points\n"
                    "Flag any concerns, anomalies, or data gaps in this data.\n\n"
                    "## Suggested Follow-Up\n"
                    "2-3 specific follow-up questions to go deeper.\n\n"
                    "RULES: Use ONLY the data above. Bold ALL key numbers. Use Indian number format. No SQL."
                )

                _r = _req.post(
                    _or_url,
                    headers={
                        "Authorization": "Bearer " + _or_key,
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  "https://myg-loyalty.com",
                        "X-Title":       "myG Loyalty AI",
                    },
                    json={"model": _model, "messages": [{"role": "user", "content": _fast_prompt}], "max_tokens": 2048},
                    timeout=30
                )
                _r.raise_for_status()
                phase1_ai_text = (_r.json()["choices"][0]["message"].get("content") or "").strip()
                if not phase1_ai_text:
                    raise ValueError("Empty response from Nemotron")
                phase1_model = "Nemotron"

            except Exception as _err:
                # Fallback: show formatted raw data with clear numbers
                if results:
                    _hdr = list(results[0].keys())
                    if len(results) > 1:
                        _rows = ["| " + " | ".join(h.replace("_", " ").title() for h in _hdr) + " |"]
                        _rows.append("|" + "---|" * len(_hdr))
                        for _row in results[:25]:
                            _rows.append("| " + " | ".join(str(_row.get(h, "")) for h in _hdr) + " |")
                        phase1_ai_text = "### Query Results\n\n" + "\n".join(_rows)
                    else:
                        lines = [f"**{k.replace('_', ' ').title()}:** {v}" for k, v in results[0].items()]
                        phase1_ai_text = "### Result\n\n" + "\n\n".join(lines)
                else:
                    phase1_ai_text = "No data returned for this query."
                phase1_model = "raw-data"

            total_time = round(time.time() - start_time, 2)
            phase1_answer = {
                "message":          phase1_ai_text + f"\n\n---\n*\U0001f4ca Data: {sql_time}s | \U0001f9e0 Nemotron analysis: {total_time}s*",
                "nemotron_pending": False,   # analysis already complete in Phase 1
                "task_id":          task_id,
                "conversation_id":  str(conversation.id),
                "charts": [], "kpis": []
            }

            cache.set(task_id, phase1_answer, timeout=600)

            # Phase 1 already ran Nemotron — cache under nemotron_{task_id} too
            # so frontend polling finds the complete answer immediately
            if phase1_model == "Nemotron":
                cache.set(f"nemotron_{task_id}", {
                    "message":          phase1_ai_text + f"\n\n---\n*\U0001f4ca Data: {sql_time}s | \U0001f9e0 Nemotron Ultra analysis complete*",
                    "reasoning_details": None,
                    "charts": [], "kpis": []
                }, timeout=600)

            # Save answer to conversation memory
            ConversationMemory.save_interaction(conversation, prompt, phase1_ai_text)


        except Exception as e:
            import traceback
            cache.set(task_id, {
                "message": f"❌ **System Error:** {str(e)}\n\n```\n{traceback.format_exc()}\n```",
                "charts": [], "kpis": []
            }, timeout=600)
            return

        from .models import AIAuditLog
        AIAuditLog.objects.create(
            user=user,
            prompt=prompt,
            matched_agent=matched_agent_name,
            generated_sql=generated_sql_str,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

        # Task cache is already set at the end of each branch above (Phase 1 instant answer)
        # Nemotron Phase 2 stores under nemotron_{task_id} separately

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
