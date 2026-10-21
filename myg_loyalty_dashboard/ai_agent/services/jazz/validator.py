"""
Data-validation specialist for Jazz AI.

Reviews every LLM-written SQL statement (before and after it runs) against the business rules of this database and
produces a list of checks that is (a) shown to the user as validation badges and (b) fed back to the LLM so it can
fix or caveat its answer. It never blocks a read-only query by itself – the hard safety net stays in `ch_assistant`.
"""

import re
from datetime import date
from typing import Any, Dict, List

from . import common as C

_SALES_TABLES = re.compile(r"\b(azure_sales_report|azure_invoice_report)\b", re.I)
_DATE_PREDICATE = re.compile(r"(toDate\s*\(\s*(\w+\.)?date\s*\)|\b(\w+\.)?date)\s*(>=|<=|!=|>|<|=|\b(between|in)\b)|\b(today|yesterday|now)\s*\(|toStartOf\w+\(|toYear\(|toMonth\(", re.I)
_REVENUE_COL = re.compile(r"\b(sold_price|invoice_total|taxable)\b", re.I)
_DATE_LITERAL = re.compile(r"'(20\d\d-\d\d-\d\d)")


def _check(name: str, status: str, detail: str) -> Dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def review_sql(sql: str) -> List[Dict[str, str]]:
    """Static review before execution."""
    checks: List[Dict[str, str]] = [_check("Read-only", "ok", "Single read-only statement; server enforces readonly mode.")]
    low = sql or ""
    touches_big = bool(_SALES_TABLES.search(low))

    if touches_big:
        if _DATE_PREDICATE.search(low):
            checks.append(_check("Date filter", "ok", "Query is bounded by a date condition."))
        else:
            checks.append(_check("Date filter", "warn", "No date condition on a very large table – result covers all history and may be slow."))

        if _REVENUE_COL.search(low) and re.search(r"\b(sum|avg)\s*\(", low, re.I):
            if re.search(r"SMC", low, re.I) and re.search(r"'3GH'|HEAD OFFICE", low, re.I):
                checks.append(_check("Internal-data exclusion", "ok", "Internal/transfer invoices are excluded like on the dashboards."))
            else:
                checks.append(_check("Internal-data exclusion", "warn",
                                     "Revenue is aggregated without the SMC/EI/HEAD OFFICE exclusion used on the dashboards – "
                                     "totals may differ from the portal."))
        try:
            latest = C.latest_sales_date()
            future = [d for d in _DATE_LITERAL.findall(low) if d > f"{latest}"]
            if future:
                checks.append(_check("Data freshness", "warn",
                                     f"Query asks for dates after the latest loaded data ({latest:%d %b %Y}); later days are missing."))
        except Exception:  # noqa: BLE001
            pass
    return checks


def review_result(res: Dict[str, Any], checks: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Post-execution review (syntax/schema validity, empty or truncated results, all-NULL aggregates)."""
    if "error" in res:
        checks.append(_check("Syntax & schema", "fail", str(res["error"])[:300]))
        return checks
    checks.append(_check("Syntax & schema", "ok", f"Executed in {res.get('elapsed_ms', 0)} ms."))
    rows = res.get("rows") or []
    if res.get("row_count", 0) == 0:
        checks.append(_check("Result sanity", "warn", "Query returned 0 rows – check filters, spelling of names and date range."))
    else:
        if rows and all(all(v in (None, 0, 0.0) for v in r) for r in rows[:5]) and len(rows) <= 5:
            checks.append(_check("Result sanity", "warn", "All returned values are zero/NULL – the filter may match nothing."))
        else:
            checks.append(_check("Result sanity", "ok", f"{res.get('row_count', 0):,} row(s) returned."))
    if res.get("truncated"):
        checks.append(_check("Row cap", "warn", f"Result truncated to the first {len(rows)} rows for display."))
    return checks


def worst_status(checks: List[Dict[str, str]]) -> str:
    st = {c["status"] for c in checks}
    return "fail" if "fail" in st else "warn" if "warn" in st else "ok"


def notes_for_llm(checks: List[Dict[str, str]]) -> List[str]:
    return [f"{c['name']}: {c['detail']}" for c in checks if c["status"] in ("warn", "fail")]
