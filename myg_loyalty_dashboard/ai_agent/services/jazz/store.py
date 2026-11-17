"""
Jazz AI persistence helpers: audit/history log, feedback, golden-query retrieval (few-shot RAG), semantic metrics,
saved prompts and a per-user rate limiter.

Every function degrades gracefully (returns an empty/neutral value) if the tables are not migrated yet or the DB is
unavailable, so a storage problem can never break a chat answer.
"""

import logging
import math
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

RATE_LIMIT_PER_MIN = 12          # questions per user per minute
RATE_LIMIT_PER_DAY = 600         # questions per user per day

_STOP = set(
    "a an the of for in on at to is are was were be by with and or me my our show give list what which who how much many "
    "this that these those it its from as do does did can you please tell about all any".split()
)


# ── Rate limit ────────────────────────────────────────────────────────────────
def check_rate_limit(user_id: Any) -> Optional[str]:
    """Returns an error message when the user is over quota, else None. Counts this call."""
    try:
        now = time.time()
        k_min, k_day = f"jazz:rl:min:{user_id}:{int(now // 60)}", f"jazz:rl:day:{user_id}:{int(now // 86400)}"
        n_min = cache.get(k_min, 0) + 1
        n_day = cache.get(k_day, 0) + 1
        cache.set(k_min, n_min, 90)
        cache.set(k_day, n_day, 90000)
        if n_min > RATE_LIMIT_PER_MIN:
            return f"Too many questions – please wait a moment (limit {RATE_LIMIT_PER_MIN} per minute)."
        if n_day > RATE_LIMIT_PER_DAY:
            return f"Daily Jazz AI limit reached ({RATE_LIMIT_PER_DAY} questions)."
    except Exception:  # noqa: BLE001
        logger.debug("rate limit check skipped", exc_info=True)
    return None


# ── Audit / history ───────────────────────────────────────────────────────────
def log_turn(user, session_id: str, question: str, result: Dict[str, Any], had_file: bool = False,
             error: str = "") -> Optional[int]:
    """Persist one turn. Returns the log id (used by the UI for feedback) or None."""
    try:
        from ai_agent.models import JazzChatLog
        steps = result.get("steps") or []
        sqls = [s.get("sql") for s in steps if s.get("tool") in ("run_sql", "query_uploaded_file") and s.get("sql")]
        agents = sorted({s.get("agent") for s in steps if s.get("agent")})
        rows = sum((r.get("row_count") or 0) for r in (result.get("results") or []))
        log = JazzChatLog.objects.create(
            user=user, session_id=(session_id or "")[:80], question=question[:4000],
            answer=(result.get("answer") or "")[:20000], sql_list=sqls[:12], tools_used=agents,
            model_used=(result.get("model") or "")[:80], elapsed_ms=int(result.get("elapsed_ms") or 0),
            rows_returned=int(rows), cache_hits=int(result.get("cache_hits") or 0),
            had_error=bool(error), error_text=error[:2000], had_file=had_file,
        )
        return log.id
    except Exception:  # noqa: BLE001
        logger.warning("Jazz log_turn failed", exc_info=True)
        return None


def save_feedback(user, log_id: int, rating: int, comment: str = "") -> Dict[str, Any]:
    from ai_agent.models import JazzChatLog, JazzGoldenQuery
    log = JazzChatLog.objects.filter(id=log_id, user=user).first()
    if not log:
        return {"error": "Answer not found."}
    rating = 1 if rating > 0 else -1
    log.rating = rating
    log.feedback_comment = (comment or "")[:2000]
    log.save(update_fields=["rating", "feedback_comment"])
    promoted = False
    if rating == 1 and log.sql_list and not log.had_error:
        # helpful answers become golden-query candidates; staff thumbs-up are trusted immediately
        if not JazzGoldenQuery.objects.filter(source_log=log).exists():
            JazzGoldenQuery.objects.create(
                question=log.question, sql=log.sql_list[-1], approved=bool(getattr(user, "is_staff", False)),
                source_log=log, created_by=user,
            )
            promoted = bool(getattr(user, "is_staff", False))
    return {"status": "ok", "promoted": promoted}


def recent_history(user, limit: int = 40) -> List[Dict[str, Any]]:
    try:
        from ai_agent.models import JazzChatLog
        qs = JazzChatLog.objects.filter(user=user, had_error=False).order_by("-created_at")[:limit]
        return [{"id": l.id, "question": l.question, "answer": l.answer, "rating": l.rating,
                 "ts": l.created_at.strftime("%d %b %H:%M")} for l in qs]
    except Exception:  # noqa: BLE001
        logger.warning("Jazz history failed", exc_info=True)
        return []


# ── Saved prompts ─────────────────────────────────────────────────────────────
def list_saved(user) -> List[Dict[str, Any]]:
    try:
        from ai_agent.models import JazzSavedPrompt
        return [{"id": p.id, "prompt": p.prompt} for p in JazzSavedPrompt.objects.filter(user=user)[:100]]
    except Exception:  # noqa: BLE001
        return []


def add_saved(user, prompt: str) -> Dict[str, Any]:
    from ai_agent.models import JazzSavedPrompt
    prompt = (prompt or "").strip()[:2000]
    if not prompt:
        return {"error": "Empty prompt."}
    obj, _ = JazzSavedPrompt.objects.get_or_create(user=user, prompt=prompt)
    return {"id": obj.id, "prompt": obj.prompt}


def delete_saved(user, pid: int) -> None:
    from ai_agent.models import JazzSavedPrompt
    JazzSavedPrompt.objects.filter(user=user, id=pid).delete()


# ── Semantic layer (business metrics) ─────────────────────────────────────────
def metrics_text() -> str:
    try:
        from ai_agent.models import JazzMetric
        rows = list(JazzMetric.objects.filter(active=True)[:60])
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    out = ["# Business metric definitions (authoritative – ALWAYS use these when the user mentions the KPI)"]
    for m in rows:
        line = f"- **{m.name}**: {m.definition.strip()}"
        if m.sql_hint.strip():
            line += f"\n  How to compute: {m.sql_hint.strip()}"
        out.append(line)
    return "\n".join(out) + "\n"


# ── Golden queries (few-shot retrieval) ───────────────────────────────────────
def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", (text or "").lower()) if w not in _STOP and len(w) > 1]


def rank_golden(question: str, items: List[Dict[str, Any]], k: int = 3, min_score: float = 0.25) -> List[Dict[str, Any]]:
    """Pure TF-IDF cosine ranking (no external deps). items = [{id, question, sql}, ...]."""
    q_tok = _tokens(question)
    if not q_tok or not items:
        return []
    docs = [_tokens(i["question"]) for i in items]
    n = len(docs) + 1
    df: Counter = Counter()
    for d in docs + [q_tok]:
        df.update(set(d))
    idf = {t: math.log(n / (1 + c)) + 1 for t, c in df.items()}

    def vec(toks):
        tf = Counter(toks)
        return {t: c * idf.get(t, 1.0) for t, c in tf.items()}

    def cos(a, b):
        num = sum(a[t] * b.get(t, 0.0) for t in a)
        da, db = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
        return num / (da * db) if da and db else 0.0

    qv = vec(q_tok)
    scored = sorted(((cos(qv, vec(d)), i) for d, i in zip(docs, items)), key=lambda x: -x[0])
    return [dict(i, score=round(s, 3)) for s, i in scored[:k] if s >= min_score]


def golden_examples_text(question: str, k: int = 3) -> str:
    try:
        from ai_agent.models import JazzGoldenQuery
        items = list(JazzGoldenQuery.objects.filter(approved=True).values("id", "question", "sql")[:400])
    except Exception:  # noqa: BLE001
        return ""
    best = rank_golden(question, items, k=k)
    if not best:
        return ""
    try:
        from django.db.models import F
        JazzGoldenQuery.objects.filter(id__in=[b["id"] for b in best]).update(times_used=F("times_used") + 1)
    except Exception:  # noqa: BLE001
        pass
    out = ["# Verified examples (approved by the team for similar questions – adapt them, do not copy blindly; "
           "re-check dates/filters for the current question)"]
    for b in best:
        out.append(f"Q: {b['question'].strip()[:300]}\n```sql\n{b['sql'].strip()[:1800]}\n```")
    return "\n".join(out) + "\n"
