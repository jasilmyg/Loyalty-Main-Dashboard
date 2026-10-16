"""
ClickHouse Assistant — engine with File Intelligence
-----------------------------------------------------
A chatbot brain that can answer questions about:
  • ClickHouse database: live queries, schema exploration, system diagnostics.
  • Uploaded files: Excel (.xlsx, .xls), CSV (.csv), and PDF (.pdf) documents.
    - Tabular files (Excel, CSV) are loaded into in-memory DuckDB for exact, zero-truncation
      calculations, groupings, filtering, and cross-referencing with ClickHouse.
    - PDF documents are parsed page-by-page and processed with multimodal document intelligence.
    - In-memory cache retains file context across chat turns for seamless follow-ups.

Safety (defence in depth):
  1. Static SQL validation for ClickHouse (single statement, read-only verbs only).
  2. Every ClickHouse query is sent with `readonly=1`.
  3. DuckDB queries on uploaded files are restricted to read-only statements.
  4. Execution-time and row caps on all queries.

LLM: Google Gemini REST API (with free-tier model fallback list).
"""

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

from . import file_analyzer

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
# Ordered fallback list – prioritize models with active available quota.
GEMINI_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-3.7-flash,gemini-3.1-flash-lite,gemini-3.6-flash,gemini-3-flash-preview,gemini-3.5-flash",
    ).split(",") if m.strip()
]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

MAX_TOOL_STEPS = 6            # max SQL/inspect calls per question
OVERALL_DEADLINE_SEC = 100    # hard stop for one question (gunicorn timeout is 120s)
LLM_ROWS_LIMIT = 40           # rows of each result shown to the LLM
UI_ROWS_LIMIT = 500           # rows of each result returned to the browser
LLM_RESULT_CHAR_LIMIT = 7000  # char cap of a tool result passed back to the LLM
MAX_HISTORY_TURNS = 10        # previous chat messages sent for context

CH_QUERY_SETTINGS = {
    "readonly": 1,                      # server-side guarantee: no writes, no DDL
    "max_execution_time": 30,           # seconds
    "max_result_rows": 10000,
    "result_overflow_mode": "break",
}

SYSTEM_DBS = ("system", "INFORMATION_SCHEMA", "information_schema")

# ── SQL safety ────────────────────────────────────────────────────────────────
_ALLOWED_START = ("SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "EXPLAIN", "EXISTS")
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|ATTACH|DETACH|"
    r"OPTIMIZE|KILL|SET|USE|EXCHANGE|UNDROP|MOVE|BACKUP|RESTORE)\b",
    re.IGNORECASE,
)
_TABLE_FUNCS = re.compile(
    r"\b(url|urlCluster|file|s3|s3Cluster|gcs|hdfs|remote|remoteSecure|mysql|postgresql|mongodb|redis|"
    r"jdbc|odbc|sqlite|azureBlobStorage|deltaLake|iceberg|hudi|input|executable|cluster|clusterAllReplicas|"
    r"dictionary)\s*\(",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _strip_literals_and_comments(sql: str) -> str:
    s = _STRING_LITERAL.sub("''", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def validate_sql(sql: str) -> Tuple[bool, str, str]:
    """Returns (ok, cleaned_sql_or_empty, error_message)."""
    if not sql or not sql.strip():
        return False, "", "Empty SQL."
    cleaned = sql.strip().rstrip(";").strip()
    probe = _strip_literals_and_comments(cleaned)
    if ";" in probe:
        return False, "", "Only a single SQL statement is allowed."
    first = probe.strip().split(None, 1)[0].upper() if probe.strip() else ""
    if first not in _ALLOWED_START:
        return False, "", f"Only read-only queries are allowed ({', '.join(_ALLOWED_START)}). Got: {first or 'nothing'}."
    m = _FORBIDDEN.search(probe)
    if m:
        return False, "", f"Forbidden keyword '{m.group(1).upper()}' – this assistant is read-only."
    m = _TABLE_FUNCS.search(probe)
    if m:
        return False, "", f"Table function '{m.group(1)}()' is not allowed."
    return True, cleaned, ""


# ── ClickHouse execution (low level) ──────────────────────────────────────────
def _clean_val(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def run_readonly_query(sql: str) -> Dict[str, Any]:
    """Execute one read-only ClickHouse SQL statement. Returns a JSON-serializable dict."""
    ok, cleaned_sql, err = validate_sql(sql)
    if not ok:
        return {"error": err, "sql": sql, "row_count": 0, "columns": [], "rows": [], "elapsed_ms": 0}

    t0 = time.time()
    try:
        from analytics.clickhouse_service import get_ch_client
        client = get_ch_client()
        if not client:
            raise RuntimeError("ClickHouse client could not connect.")
        result = client.query(cleaned_sql, settings=CH_QUERY_SETTINGS)
    except Exception as e:  # noqa: BLE001
        logger.warning("ClickHouse query failed: %s; SQL: %s", e, cleaned_sql[:300])
        return {
            "error": str(e),
            "sql": cleaned_sql,
            "row_count": 0,
            "columns": [],
            "rows": [],
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    elapsed_ms = int((time.time() - t0) * 1000)
    cols = list(result.column_names)
    raw_rows = result.result_rows or []
    truncated = len(raw_rows) > UI_ROWS_LIMIT
    rows = [[_clean_val(c) for c in r] for r in raw_rows[:UI_ROWS_LIMIT]]
    return {
        "columns": cols,
        "rows": rows,
        "row_count": len(raw_rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "sql": cleaned_sql,
        "source": "ClickHouse Cloud",
    }


# ── Schema metadata cache ─────────────────────────────────────────────────────
_schema_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_schema_lock = threading.Lock()
SCHEMA_TTL_SEC = 600  # 10 minutes


def _human(n: Optional[int]) -> str:
    if n is None:
        return "–"
    for unit in ["", "K", "M", "B"]:
        if abs(n) < 1000:
            return f"{n:.1f}{unit}".rstrip("0").rstrip(".") if unit else f"{n}"
        n /= 1000.0
    return f"{n:.1f}T"


def get_schema(force: bool = False) -> Dict[str, Any]:
    """Return catalog of all non-system tables, engines, rows, sizes and columns."""
    with _schema_lock:
        if not force and _schema_cache["data"] and (time.time() - _schema_cache["ts"]) < SCHEMA_TTL_SEC:
            return _schema_cache["data"]

    skip = ", ".join(f"'{d}'" for d in SYSTEM_DBS)
    t = run_readonly_query(
        f"SELECT database, name, engine, total_rows, total_bytes, sorting_key, partition_key, comment "
        f"FROM system.tables WHERE database NOT IN ({skip}) ORDER BY database, name"
    )
    c = run_readonly_query(
        f"SELECT database, `table`, name, type, comment "
        f"FROM system.columns WHERE database NOT IN ({skip}) ORDER BY database, `table`, position"
    )
    if "error" in t or "error" in c:
        raise RuntimeError((t.get("error") or c.get("error")))

    cols_by_table: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for db, tbl, name, typ, comment in c["rows"]:
        cols_by_table.setdefault((db, tbl), []).append({"name": name, "type": typ, "comment": comment or ""})
    tables = []
    for db, name, engine, rows, nbytes, skey, pkey, comment in t["rows"]:
        tables.append({
            "database": db, "name": name, "engine": engine, "rows": rows, "bytes": nbytes,
            "sorting_key": skey or "", "partition_key": pkey or "", "comment": comment or "",
            "columns": cols_by_table.get((db, name), []),
        })
    data = {"tables": tables, "loaded_at": datetime.utcnow().isoformat() + "Z"}
    with _schema_lock:
        _schema_cache.update(ts=time.time(), data=data)
    return data


def schema_as_prompt_text() -> str:
    lines = []
    for t in get_schema()["tables"]:
        fq = f"{t['database']}.{t['name']}"
        meta = [t["engine"], f"{_human(t['rows'])} rows"]
        if t["sorting_key"]:
            meta.append(f"ORDER BY {t['sorting_key']}")
        if t["partition_key"]:
            meta.append(f"PARTITION BY {t['partition_key']}")
        head = f"- {fq} [{'; '.join(meta)}]"
        if t["comment"]:
            head += f" -- {t['comment']}"
        lines.append(head)
        lines.append("    cols: " + ", ".join(
            f"{c['name']} {c['type']}" + (f" /*{c['comment']}*/" if c["comment"] else "") for c in t["columns"]
        ))
    return "\n".join(lines)


# ── Prompt ────────────────────────────────────────────────────────────────────
def build_system_prompt(file_ctx: Optional[Dict[str, Any]] = None) -> str:
    try:
        schema_txt = schema_as_prompt_text()
    except Exception as e:  # noqa: BLE001
        schema_txt = f"(schema could not be loaded right now: {e}. Use inspect_table / run_sql on system.tables.)"
    today = datetime.now().strftime("%Y-%m-%d")
    base_prompt = f"""You are **Jazz AI**, the assistant for the myG Loyalty portal – an expert in ClickHouse, business data analytics, and spreadsheets/documents/images (myG retail stores in Kerala, India; amounts are in Indian Rupees ₹). Today is {today}.

You can answer ANY question related to:
1. DATA questions (sales, customers, branches, stock, loyalty, pine-lab, OSG, market-basket …) → use the `run_sql` tool.
2. SCHEMA / STRUCTURE questions (what tables exist, columns, sizes, engines, keys, relationships) → use the schema below,
   and `inspect_table` for samples / exact DDL.
3. GENERAL ClickHouse questions (SQL syntax, functions, engines, MergeTree, performance tuning, best practices,
   errors) → answer from your expertise.
4. UPLOADED FILES & IMAGES (Excel, CSV, PDF, Images) → analyze files in-depth, perform calculations, aggregations, data profiling,
   and multimodal vision analysis. For images (screenshots, invoices, receipts, charts, photos), inspect all visual details,
   perform OCR, read data tables/graphs, and answer questions or cross-reference against ClickHouse using `run_sql`.

# Tool rules
- NEVER invent numbers, table names or columns. For anything data-related you MUST query first.
- Only read-only queries are permitted.
- Use ClickHouse SQL dialect for `run_sql`.
- Prefer aggregations, add ORDER BY and LIMIT (default LIMIT 50 unless the user wants everything). Never SELECT * on big tables without LIMIT.
- If a query fails, read the error, fix the SQL and retry (you have at most {MAX_TOOL_STEPS} tool calls per question).

# Business conventions of this database
- Placeholder rows with date = '1970-01-01' are invalid – exclude them from time-based analysis.
- The main dashboards EXCLUDE internal/transfer data: invoices whose invoice_no contains 'SMC' or 'EI' and branches
  'HEAD OFFICE', 'UG SMART CHOICE', '3GH'. For sales/revenue questions on azure_invoice_report / azure_sales_report apply:
    NOT (upper(invoice_no) LIKE '%SMC%' OR upper(invoice_no) LIKE '%EI%' OR upper(branch) IN ('HEAD OFFICE','UG SMART CHOICE','3GH'))
- azure_invoice_report = one row per invoice; azure_sales_report = one row per item sold (join on invoice_no);
  item_master (item_code → product/brand/category); branch_master (branch code → name, RBM, BDM, district).

# Answer style
- Be concise, analytical, and executive-ready. Lead with the direct answer, followed by key metrics & insights.
- Format money as ₹ with Indian grouping when natural (₹12,34,567), large totals may use Lakh/Crore in brackets.
- The raw result table is shown to the user automatically below your answer – do NOT repeat all rows; summarise
  or show only the top few in your markdown.
- Customer mobile numbers are personal data: show them only if explicitly asked, otherwise aggregate.

# Live database schema (ClickHouse server, database `default`)
{schema_txt}
"""

    if file_ctx:
        fname = file_ctx.get("filename", "Uploaded File")
        ftype = file_ctx.get("file_type", "").upper()
        fsize = file_ctx.get("file_size_str", "")
        summary_prompt = file_ctx.get("summary_prompt", "")
        is_image = file_ctx.get("file_type") == "image"

        image_rules = """
## RULES FOR MULTIMODAL IMAGE ANALYSIS:
1. The user has attached an image as native multimodal visual input to Gemini.
2. Carefully inspect the image: transcribe any text, numbers, metrics, charts, labels, tables, or visual data.
3. If the image depicts business reports, POS receipts, ERP screens, invoices, graphs, or target vs actuals:
   - Accurately extract the key figures and explain what is shown.
   - Point out key trends, anomalies, totals, or comparisons.
4. If the user asks to verify, reconcile, or compare the image with ClickHouse data:
   - You MUST query live ClickHouse tables using `run_sql` and compare actual database numbers with the figures in the image!
5. Structure your response with an Executive Summary, Extracted Data / Key Findings, and Insights.
""" if is_image else """
## RULES FOR UPLOADED FILE OPERATIONS:
1. Carefully perform whatever operation or analysis the user requested (e.g., summaries, top performers, calculations, comparisons, filtering).
2. If this is an Excel or CSV file, and the user asks for exact aggregations, rankings, calculations, or filtering:
   YOU MUST CALL the `query_uploaded_file` tool! In-memory DuckDB executes ANSI SQL on all rows without truncation.
   Available tables: {tables}.
3. If the user asks to compare or cross-reference the uploaded file with ClickHouse database data:
   You have BOTH `query_uploaded_file` (for the uploaded file) AND `run_sql` (for ClickHouse) available! Execute both and synthesize the findings.
4. Structure your response with:
   - 📊 **Executive Summary**: Direct answer and key takeaways
   - 📈 **Detailed Operations / Findings**: Groupings, metrics, and comparisons (use Markdown tables where helpful)
   - 💡 **Key Insights & Recommendations**: Meaningful business observations, anomalies, or follow-up opportunities
""".format(tables=', '.join(f'`{t}`' for t in file_ctx.get('tables', ['uploaded_file'])))

        base_prompt += f"""

# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE UPLOADED FILE CONTEXT: `{fname}` ({ftype}, {fsize})
# ══════════════════════════════════════════════════════════════════════════════
The user uploaded `{fname}` to perform analysis, answer questions, or execute specific operations on it.

{summary_prompt}

{image_rules}
"""
    return base_prompt


# ── Tools exposed to Gemini ───────────────────────────────────────────────────
def get_tools_declaration(file_ctx: Optional[Dict[str, Any]] = None) -> List[Dict]:
    funcs = [
        {
            "name": "run_sql",
            "description": (
                "Run ONE read-only ClickHouse SQL query (SELECT/WITH/DESCRIBE/SHOW/EXPLAIN) and get columns + rows. "
                "Server enforces readonly mode; results are capped. Use for all ClickHouse database questions."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {"sql": {"type": "STRING", "description": "A single ClickHouse SQL statement."}},
                "required": ["sql"],
            },
        },
        {
            "name": "inspect_table",
            "description": "Get the exact CREATE TABLE DDL, columns and a few sample rows of one ClickHouse table (e.g. 'azure_sales_report').",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "table": {"type": "STRING", "description": "Table name, optionally db-qualified."},
                    "sample_rows": {"type": "INTEGER", "description": "How many sample rows (0-5). Default 3."},
                },
                "required": ["table"],
            },
        },
    ]

    if file_ctx and file_ctx.get("duckdb_conn"):
        tables_str = ", ".join(f"'{t}'" for t in file_ctx.get("tables", ["uploaded_file"]))
        funcs.append({
            "name": "query_uploaded_file",
            "description": (
                f"Run an in-memory ANSI SQL query (DuckDB) on the uploaded spreadsheet or CSV tables ({tables_str}). "
                "Use this to compute exact sums, averages, rankings, counts, group bys, and filters across ALL rows."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "sql": {
                        "type": "STRING",
                        "description": (
                            "DuckDB ANSI SQL query on 'uploaded_file' or sheet tables "
                            "(e.g., SELECT column_name, SUM(amount) FROM uploaded_file GROUP BY column_name ORDER BY 2 DESC LIMIT 50)."
                        ),
                    }
                },
                "required": ["sql"],
            },
        })

    return [{"functionDeclarations": funcs}]


def _result_for_llm(res: Dict[str, Any]) -> Dict[str, Any]:
    if "error" in res:
        return {"error": res["error"]}
    out = {
        "columns": res["columns"],
        "rows": res["rows"][:LLM_ROWS_LIMIT],
        "total_rows_returned": res["row_count"],
        "shown_rows": min(res["row_count"], LLM_ROWS_LIMIT),
        "elapsed_ms": res["elapsed_ms"],
    }
    txt = json.dumps(out, default=str)
    while len(txt) > LLM_RESULT_CHAR_LIMIT and out["rows"]:
        out["rows"] = out["rows"][: max(1, len(out["rows"]) // 2)]
        out["shown_rows"] = len(out["rows"])
        txt = json.dumps(out, default=str)
        if len(out["rows"]) == 1:
            break
    return out


def _tool_run_sql(args: Dict[str, Any], ui_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    res = run_readonly_query(str(args.get("sql", "")))
    ui_results.append(res)
    return _result_for_llm(res)


def _tool_inspect_table(args: Dict[str, Any], ui_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    table = str(args.get("table", "")).strip().strip("`")
    if not _IDENT.match(table):
        return {"error": "Invalid table name."}
    try:
        n = max(0, min(5, int(args.get("sample_rows", 3))))
    except (TypeError, ValueError):
        n = 3
    ddl = run_readonly_query(f"SHOW CREATE TABLE {table}")
    out: Dict[str, Any] = {}
    if "error" in ddl:
        return {"error": ddl["error"]}
    out["create_table"] = str(ddl["rows"][0][0])[:3500] if ddl["rows"] else ""
    if n:
        sample = run_readonly_query(f"SELECT * FROM {table} LIMIT {n}")
        if "error" not in sample:
            out["sample_columns"] = sample["columns"]
            out["sample_rows"] = sample["rows"]
            ui_results.append(sample)
    return out


def _tool_query_uploaded_file(args: Dict[str, Any], ui_results: List[Dict[str, Any]], file_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not file_ctx or not file_ctx.get("duckdb_conn"):
        return {"error": "No uploaded file is active in this session."}
    sql = str(args.get("sql", ""))
    res = file_analyzer.execute_file_query(file_ctx["duckdb_conn"], sql)
    if "error" not in res and res.get("columns"):
        ui_results.append(res)
    return _result_for_llm(res)


# ── Gemini REST client ────────────────────────────────────────────────────────
class LLMError(Exception):
    pass


def _gemini_generate(contents: List[Dict], system_prompt: str, allow_tools: bool, deadline: float, tools: List[Dict]) -> Tuple[Dict, str]:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise LLMError("GEMINI_API_KEY is not configured. Add it to the .env file (free key: https://aistudio.google.com/apikey).")
    body: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192, "thinkingConfig": {"thinkingLevel": "low"}},
    }
    if allow_tools:
        body["tools"] = tools
    else:
        body["tools"] = tools
        body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}

    last_err = "unknown error"
    for model in GEMINI_MODELS:
        for attempt in range(2):
            remaining = deadline - time.time()
            if remaining < 5:
                raise LLMError("The request took too long. Please try a simpler question.")
            try:
                r = requests.post(
                    GEMINI_URL.format(model=model),
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=min(70, remaining),
                )
            except requests.RequestException as e:
                last_err = f"{model}: {e}"
                break  # try next model
            if r.status_code == 200:
                data = r.json()
                if data.get("candidates") and data["candidates"][0].get("content"):
                    return data, model
                last_err = f"{model}: empty response ({data.get('promptFeedback') or data['candidates'][0].get('finishReason', '')})"
                break
            last_err = f"{model}: HTTP {r.status_code} {r.text[:200]}"
            if r.status_code in (500, 502, 503, 504) and attempt == 0:
                time.sleep(1.0)
                continue
            break  # 429, 400, 403, 404 etc. → immediately try next model
    logger.warning("Gemini failed on all models: %s", last_err)
    if "429" in last_err:
        raise LLMError("The free Gemini quota is exhausted for the moment. Please wait a minute and try again.")
    raise LLMError(f"The AI model is unavailable right now ({last_err[:160]}).")


# ── Public entry point ────────────────────────────────────────────────────────
def _history_to_contents(history: List[Dict[str, str]]) -> List[Dict]:
    contents: List[Dict] = []
    for h in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "model" if h.get("role") in ("assistant", "model") else "user"
        text = str(h.get("content", "")).strip()[:6000]
        if not text:
            continue
        if contents and contents[-1]["role"] == role:  # Gemini requires alternating roles
            contents[-1]["parts"][0]["text"] += "\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


def _text_of(content: Dict) -> str:
    return "".join(p.get("text", "") for p in content.get("parts", []) if p.get("text") and not p.get("thought")).strip()


def ask(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    uploaded_file: Optional[Any] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Answer one question, optionally analyzing an uploaded file (Excel, CSV, or PDF)
    or executing operations requested on it.
    Returns {answer, results[], steps[], model, elapsed_ms, file_info}.
    """
    started = time.time()
    deadline = started + OVERALL_DEADLINE_SEC
    question = (question or "").strip()

    file_ctx = None
    if uploaded_file is not None:
        try:
            filename = getattr(uploaded_file, "name", "uploaded_file")
            file_ctx = file_analyzer.analyze_file(uploaded_file, filename)
            if session_id:
                file_analyzer.set_cached_file_context(session_id, file_ctx)
        except Exception as e:
            logger.exception("Failed to process uploaded file: %s", e)
            return {
                "answer": f"⚠️ Could not process uploaded file: {str(e)}",
                "results": [],
                "steps": [],
                "model": None,
                "elapsed_ms": int((time.time() - started) * 1000),
                "file_info": None,
            }
    elif session_id:
        file_ctx = file_analyzer.get_cached_file_context(session_id)

    if not question:
        if file_ctx:
            if file_ctx.get("file_type") == "image":
                question = (
                    f"Perform a comprehensive visual analysis of this uploaded image (`{file_ctx['filename']}`). "
                    "Transcribe any visible text and numbers, examine any charts, tables, or trends, and provide clear analytical insights."
                )
            else:
                question = (
                    f"Perform a comprehensive analysis of this uploaded {file_ctx['file_type'].upper()} file (`{file_ctx['filename']}`). "
                    "Highlight key trends, metrics, anomalies, and summary statistics, and present clear executive insights."
                )
        else:
            return {"answer": "Please type a question or upload a file.", "results": [], "steps": [], "model": None, "elapsed_ms": 0}

    system_prompt = build_system_prompt(file_ctx)
    tools_decl = get_tools_declaration(file_ctx)

    contents = _history_to_contents(history or [])
    if contents and contents[-1]["role"] == "user":
        contents.pop()  # the new question replaces a dangling user turn

    user_parts: List[Dict[str, Any]] = []
    # If PDF has inline_parts (base64)
    if file_ctx and file_ctx.get("inline_parts"):
        user_parts.extend(file_ctx["inline_parts"])
    user_parts.append({"text": question[:4000]})
    contents.append({"role": "user", "parts": user_parts})

    ui_results: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    used_model = None

    for _ in range(MAX_TOOL_STEPS + 1):
        force_answer = len(steps) >= MAX_TOOL_STEPS
        data, used_model = _gemini_generate(
            contents, system_prompt, allow_tools=not force_answer, deadline=deadline, tools=tools_decl
        )
        content = data["candidates"][0]["content"]
        calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]

        if not calls or force_answer:
            answer = _text_of(content) or "I could not produce an answer. Please rephrase your question."
            break

        contents.append(content)  # keep verbatim (includes thought signatures needed by Gemini 3.x)
        responses = []
        for call in calls:
            name, args = call.get("name"), call.get("args") or {}
            if name == "run_sql":
                result = _tool_run_sql(args, ui_results)
                step = {"tool": name, "sql": args.get("sql") or "", "source": "ClickHouse Cloud"}
            elif name == "inspect_table":
                result = _tool_inspect_table(args, ui_results)
                step = {"tool": name, "sql": f"inspect {args.get('table', '')}", "source": "ClickHouse Cloud"}
            elif name == "query_uploaded_file":
                result = _tool_query_uploaded_file(args, ui_results, file_ctx)
                step = {"tool": name, "sql": args.get("sql") or "", "source": "Uploaded File (DuckDB)"}
            else:
                result = {"error": f"Unknown tool {name}"}
                step = {"tool": name, "sql": "", "source": "Unknown"}

            if "error" in result:
                step["error"] = result["error"]
            steps.append(step)
            responses.append({"functionResponse": {"name": name, "response": {"result": result}}})
        contents.append({"role": "user", "parts": responses})
    else:  # pragma: no cover
        answer = "I reached the maximum number of steps before finishing. Please narrow the question."

    # Surface successful results to the UI (last successful result shown in table)
    shown = [r for r in ui_results if "error" not in r and r.get("columns")]

    file_info = None
    if file_ctx:
        file_info = {
            "name": file_ctx["filename"],
            "type": file_ctx["file_type"],
            "size_str": file_ctx["file_size_str"],
            "meta": file_ctx.get("meta", {}),
        }

    return {
        "answer": answer,
        "results": shown[-1:],
        "steps": steps,
        "model": used_model,
        "elapsed_ms": int((time.time() - started) * 1000),
        "file_info": file_info,
    }
