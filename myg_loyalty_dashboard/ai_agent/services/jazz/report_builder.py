"""
Jazz AI – Excel report builder
==============================
Turns several read-only ClickHouse queries into ONE formatted multi-sheet .xlsx workbook
(e.g. "JAS 2025 vs JAS 2026" with RBM / BDM / Branch / drill-down / Insights sheets).

Why this exists: the normal `run_sql` path caps results at 500 rows for the browser and 40 rows for the LLM, and
can only return chat text. Here every sheet is queried IN FULL (up to MAX_ROWS_PER_SHEET) and written straight to
a file – the LLM only supplies the SQL, the sheet titles and the insight bullets, it never has to see/copy the rows.

Safety: each query goes through the same validator as `run_sql` and runs with `readonly=1`.
"""

import logging
import os
import re
import tempfile
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_SHEETS = 8
MAX_ROWS_PER_SHEET = 200_000
REPORT_DIR = os.path.join(tempfile.gettempdir(), "jazz_reports")
REPORT_TTL_SEC = 6 * 3600
REPORT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

REPORT_QUERY_SETTINGS = {
    "readonly": 1,
    "max_execution_time": 55,
    "max_result_rows": MAX_ROWS_PER_SHEET + 1,
    "result_overflow_mode": "break",
}

_PCT_COL = re.compile(r"%|pct|percent|growth|gr_|achiev|share|margin", re.I)
_DELTA_COL = re.compile(r"change|delta|diff|variance|growth|gap|vs_", re.I)
_ID_COL = re.compile(r"mobile|phone|pin|hsn|code|_no$|^no$|id$|year|month|day|week|quarter|hour|imei|gstin", re.I)

# palette
_NAVY, _WHITE, _LIGHT = "1F2A5A", "FFFFFF", "EEF1FB"
_GREEN_FILL, _RED_FILL = "D9F2E3", "FBDADA"
_GREEN_FONT, _RED_FONT = "136C3B", "A61B1B"


def path_for(report_id: str) -> Optional[str]:
    if not REPORT_ID_RE.match(report_id or ""):
        return None
    p = os.path.join(REPORT_DIR, report_id + ".xlsx")
    return p if os.path.exists(p) else None


def _cleanup_old() -> None:
    try:
        now = time.time()
        for f in os.listdir(REPORT_DIR):
            fp = os.path.join(REPORT_DIR, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > REPORT_TTL_SEC:
                os.remove(fp)
    except OSError:
        pass


def _safe_name(name: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", str(name or "Sheet")).strip()[:28] or "Sheet"
    cand, n = base, 2
    while cand.lower() in used:
        cand = f"{base[:26]} {n}"
        n += 1
    used.add(cand.lower())
    return cand


def _xl_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, (date, int, float, bool)):
        return v
    if isinstance(v, str):
        return v[:32000]
    return str(v)[:32000]


def _pretty_header(c: str) -> str:
    s = str(c).replace("_", " ").strip().title() if re.fullmatch(r"[a-z0-9_]+", str(c)) else str(c)
    return re.sub(r"\b(Pct|Percent)\b", "%", s)


def _short(v: float) -> str:
    a = abs(v)
    s = "-" if v < 0 else "+"
    if a >= 1e7:
        return f"{s}{a / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"{s}{a / 1e5:,.2f} L"
    return f"{s}{a:,.0f}"


def _auto_insights(sheet_name: str, cols: List[str], rows: List[Any]) -> List[str]:
    """Data-derived highlights for summary-style sheets (a label column plus a Change column). Pure arithmetic, no LLM."""
    if not rows or len(rows) > 2000 or len(cols) < 2:
        return []
    label_i = next((i for i in range(len(cols)) if isinstance(next((r[i] for r in rows if r[i] is not None), None), str)), None)
    if label_i is None:
        return []

    def numeric(i):
        v = next((r[i] for r in rows if r[i] is not None), None)
        return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)

    abs_cols = [i for i, c in enumerate(cols) if numeric(i) and re.search(r"change|delta|diff|variance|gap", str(c), re.I) and not re.search(r"%|pct|percent", str(c), re.I)]
    out: List[str] = []
    for ci in abs_cols[:2]:
        pairs = [(str(r[label_i]), float(r[ci])) for r in rows if r[ci] is not None and r[label_i] not in (None, "")]
        if len(pairs) < 2:
            continue
        total = sum(v for _, v in pairs)
        up = sorted([p for p in pairs if p[1] > 0], key=lambda p: -p[1])[:3]
        down = sorted([p for p in pairs if p[1] < 0], key=lambda p: p[1])[:3]
        n_up, n_down = sum(1 for _, v in pairs if v > 0), sum(1 for _, v in pairs if v < 0)
        txt = f"{sheet_name} \u2013 {_pretty_header(cols[ci])}: net {_short(total)} across {len(pairs)} rows ({n_up} up, {n_down} down)."
        if up:
            txt += " Top gainers: " + ", ".join(f"{n} ({_short(v)})" for n, v in up) + "."
        if down:
            txt += " Top decliners: " + ", ".join(f"{n} ({_short(v)})" for n, v in down) + "."
        out.append(txt)
    return out


def _run_query(sql: str) -> Dict[str, Any]:
    from ..ch_assistant import validate_sql  # local import: ch_assistant imports this module
    ok, cleaned, err = validate_sql(sql)
    if not ok:
        return {"error": err}
    try:
        from analytics.starrocks_service import get_sr_client
        client = get_sr_client()
        if not client:
            return {"error": "StarRocks client could not connect."}
        res = client.query(cleaned, settings=REPORT_QUERY_SETTINGS)
    except Exception as e:  # noqa: BLE001
        logger.warning("Report query failed: %s; SQL: %s", e, cleaned[:300])
        return {"error": str(e)[:400]}
    rows = res.result_rows or []
    return {"columns": list(res.column_names), "rows": rows[:MAX_ROWS_PER_SHEET], "truncated": len(rows) > MAX_ROWS_PER_SHEET}


def build_report(title: str, sheets: List[Dict[str, Any]], insights: Optional[List[str]] = None,
                 deadline: Optional[float] = None) -> Dict[str, Any]:
    """
    sheets: [{"name": str, "sql": str, "description": str (optional)}]  – run in order, each in full.
    insights: bullet strings for the "Insights" sheet (placed first).
    Returns {report_id, filename, sheets:[{name, rows, columns, error?}], total_rows} or {"error": ...}.
    """
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    sheets = [s for s in (sheets or []) if isinstance(s, dict) and str(s.get("sql") or "").strip()]
    if not sheets:
        return {"error": "Provide at least one sheet with a name and a SQL query."}
    if len(sheets) > MAX_SHEETS:
        return {"error": f"At most {MAX_SHEETS} sheets per report."}

    title = str(title or "Jazz AI Report").strip()[:120]
    os.makedirs(REPORT_DIR, exist_ok=True)
    _cleanup_old()

    hdr_font = Font(bold=True, color=_WHITE, name="Calibri", size=11)
    hdr_fill = PatternFill("solid", fgColor=_NAVY)
    band_fill = PatternFill("solid", fgColor=_LIGHT)
    thin = Side(style="thin", color="D5DAEA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = Workbook()
    wb.remove(wb.active)
    used: set = set()
    summary: List[Dict[str, Any]] = []
    total_rows = 0
    auto_notes: List[str] = []
    for spec in sheets:
        if deadline and time.time() > deadline:
            summary.append({"name": str(spec.get("name") or "Sheet"), "rows": 0, "columns": 0,
                            "error": "Skipped – ran out of time. Ask for fewer / lighter sheets."})
            continue
        sname = _safe_name(spec.get("name"), used)
        res = _run_query(str(spec["sql"]))
        if "error" in res:
            summary.append({"name": sname, "rows": 0, "columns": 0, "error": res["error"]})
            continue

        cols, rows = res["columns"], res["rows"]
        ws = wb.create_sheet(sname)
        ncols = max(1, len(cols))

        ws.cell(row=1, column=1, value=f"{title} – {spec.get('name') or sname}").font = Font(bold=True, size=14, color=_NAVY)
        desc = str(spec.get("description") or "").strip()
        sub = (desc + "  |  " if desc else "") + f"{len(rows):,} rows"
        if res.get("truncated"):
            sub += f" (first {MAX_ROWS_PER_SHEET:,} shown)"
        ws.cell(row=2, column=1, value=sub).font = Font(italic=True, size=10, color="6B7280")

        HR = 4
        for ci, c in enumerate(cols, 1):
            cell = ws.cell(row=HR, column=ci, value=_pretty_header(c))
            cell.font, cell.fill, cell.border = hdr_font, hdr_fill, border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[HR].height = 30

        widths = [len(_pretty_header(c)) + 2 for c in cols]
        kinds: List[str] = []
        for ci, c in enumerate(cols):
            sample = next((r[ci] for r in rows[:200] if r[ci] is not None), None)
            numeric = isinstance(sample, (int, float, Decimal)) and not isinstance(sample, bool)
            if numeric and _ID_COL.search(str(c)) and not _PCT_COL.search(str(c)):
                kinds.append("id")
            elif numeric and (_PCT_COL.search(str(c))):
                kinds.append("pct")
            elif numeric:
                kinds.append("int" if isinstance(sample, int) else "num")
            elif isinstance(sample, (date, datetime)):
                kinds.append("date")
            else:
                kinds.append("text")

        for ri, r in enumerate(rows, HR + 1):
            for ci, v in enumerate(r, 1):
                v = _xl_value(v)
                cell = ws.cell(row=ri, column=ci, value=v)
                k = kinds[ci - 1]
                if k == "pct":
                    cell.number_format = '0.0"%"'  # values are percent points (12.5 = 12.5%)
                elif k == "num":
                    cell.number_format = "#,##0.00" if isinstance(v, float) and abs(v) < 1000 else "#,##0"
                elif k == "int":
                    cell.number_format = "#,##0"
                elif k == "date":
                    cell.number_format = "dd-mmm-yyyy"
                if len(rows) <= 5000:
                    cell.border = border
                    if (ri - HR) % 2 == 0:
                        cell.fill = band_fill
                if ri <= HR + 300 and v is not None:
                    widths[ci - 1] = max(widths[ci - 1], min(len(str(v)) + 2, 48))

        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(ci)].width = max(10, min(w, 48))
        ws.freeze_panes = ws.cell(row=HR + 1, column=2 if ncols > 1 else 1)
        if rows:
            ws.auto_filter.ref = f"A{HR}:{get_column_letter(ncols)}{HR + len(rows)}"
            last = HR + len(rows)
            for ci, c in enumerate(cols, 1):
                if kinds[ci - 1] in ("pct", "num", "int") and _DELTA_COL.search(str(c)):
                    rng = f"{get_column_letter(ci)}{HR + 1}:{get_column_letter(ci)}{last}"
                    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"],
                                                  fill=PatternFill("solid", bgColor=_RED_FILL, fgColor=_RED_FILL), font=Font(color=_RED_FONT)))
                    ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0"],
                                                  fill=PatternFill("solid", bgColor=_GREEN_FILL, fgColor=_GREEN_FILL), font=Font(color=_GREEN_FONT)))
        ws.sheet_view.showGridLines = False
        total_rows += len(rows)
        auto_notes.extend(_auto_insights(spec.get("name") or sname, cols, rows))
        summary.append({"name": sname, "rows": len(rows), "columns": len(cols), "truncated": bool(res.get("truncated")),
                        "description": desc,
                        "preview": {"columns": cols, "rows": [[_xl_value(x) if not isinstance(_xl_value(x), (date, datetime)) else str(x) for x in r] for r in rows[:12]]}})

    built = [s for s in summary if not s.get("error")]
    if not built:
        return {"error": "No sheet could be built.", "sheets": summary}

    # Insights sheet (first)
    bullets = ([str(i).strip() for i in (insights or []) if str(i).strip()] + auto_notes)[:40]
    ws = wb.create_sheet("Insights", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 120
    ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=16, color=_NAVY)
    ws.cell(row=2, column=1, value=f"Generated by Jazz AI on {datetime.now():%d-%b-%Y %H:%M}").font = Font(italic=True, size=10, color="6B7280")
    r = 4
    if bullets:
        c = ws.cell(row=r, column=1, value="Key insights")
        c.font = Font(bold=True, size=12, color=_WHITE)
        for cc in (1, 2):
            ws.cell(row=r, column=cc).fill = hdr_fill
        r += 1
        for i, b in enumerate(bullets, 1):
            ws.cell(row=r, column=1, value=i).alignment = Alignment(vertical="top", horizontal="center")
            cell = ws.cell(row=r, column=2, value=b)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = max(18, 15 * (1 + len(b) // 115))
            r += 1
        r += 1
    c = ws.cell(row=r, column=1, value="Sheets in this workbook")
    c.font = Font(bold=True, size=12, color=_WHITE)
    for cc in (1, 2):
        ws.cell(row=r, column=cc).fill = hdr_fill
    r += 1
    for s in built:
        ws.cell(row=r, column=1, value="•").alignment = Alignment(horizontal="center")
        ws.cell(row=r, column=2, value=f"{s['name']} – {s['rows']:,} rows" + (f" – {s['description']}" if s.get("description") else ""))
        ws.cell(row=r, column=2).hyperlink = f"#'{s['name']}'!A1"
        ws.cell(row=r, column=2).font = Font(color="1D4ED8", underline="single")
        r += 1

    report_id = uuid.uuid4().hex
    safe_title = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip().replace(" ", "_")[:60] or "Jazz_Report"
    filename = f"{safe_title}.xlsx"
    wb.save(os.path.join(REPORT_DIR, report_id + ".xlsx"))
    return {"report_id": report_id, "filename": filename, "title": title, "sheets": summary, "auto_insights": auto_notes[:10],
            "total_rows": total_rows, "size_bytes": os.path.getsize(os.path.join(REPORT_DIR, report_id + ".xlsx"))}
