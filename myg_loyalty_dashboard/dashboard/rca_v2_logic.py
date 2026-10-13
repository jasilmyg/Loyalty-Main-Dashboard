import pandas as pd
from analytics.clickhouse_service import get_ch_client
from dashboard.utils import RETAIL_BRANCH_FILTER
import difflib
import statistics
from collections import defaultdict
from django.core.cache import cache

# Parsed review date (updated_at is stored as a free-form string)
DATE = "toDate(parseDateTimeBestEffortOrNull(replaceRegexpAll(updated_at, ',|Thu|Fri|Sat|Sun|Mon|Tue|Wed', '')))"

# Primary root causes that are NOT complaints (never rank these as a 'driver')
NON_COMPLAINT = ("Positive Feedback & Good Service", "General Inquiry", "Empty/No Comment", "N/A")
NON_COMPLAINT_SQL = ", ".join("'" + c.replace("'", "''") + "'" for c in NON_COMPLAINT)
COMPLAINT_COND = f"dim_primary_root_cause NOT IN ({NON_COMPLAINT_SQL})"


def _store_cond(request):
    store = request.GET.get('store', 'all')
    if store and store != 'all':
        return ["store_name = '" + store.replace("'", "''") + "'"]
    return []


def _where(request, *extra):
    conds = _store_cond(request) + [e for e in extra if e]
    return (" WHERE " + " AND ".join(conds)) if conds else ""


def _pct(a, b, nd=1):
    return round(a / b * 100, nd) if b else 0


def _ins(kind, title, text):
    """kind: danger | warning | success | info"""
    return {'type': kind, 'title': title, 'text': text}


def get_period_comparison(ch, request):
    """Last 30 days of available data vs. the 30 days before it."""
    W = _where(request)
    maxd = ch.query(f"SELECT max({DATE}) FROM google_reviews{W}").result_rows[0][0]
    if not maxd:
        return None
    rows = ch.query(f"""
        SELECT multiIf(d > subtractDays(toDate('{maxd}'), 30), 'cur',
                       d > subtractDays(toDate('{maxd}'), 60), 'prev', 'old') AS p,
               count(), sum(rating <= 2), sum(rating >= 4)
        FROM (SELECT {DATE} AS d, rating FROM google_reviews{W})
        GROUP BY p
    """).result_rows
    by = {r[0]: r for r in rows}
    cur = by.get('cur', ('cur', 0, 0, 0))
    prev = by.get('prev', ('prev', 0, 0, 0))
    cur_neg, prev_neg = _pct(cur[2], cur[1]), _pct(prev[2], prev[1])
    cur_pos, prev_pos = _pct(cur[3], cur[1]), _pct(prev[3], prev[1])
    return {
        'as_of': str(maxd),
        'cur_total': cur[1], 'prev_total': prev[1],
        'cur_neg': cur[2], 'prev_neg': prev[2],
        'total_delta_pct': _pct(cur[1] - prev[1], prev[1]),
        'neg_rate_cur': cur_neg, 'neg_rate_prev': prev_neg,
        'neg_delta_pp': round(cur_neg - prev_neg, 1),
        'pos_rate_cur': cur_pos, 'pos_rate_prev': prev_pos,
        'pos_delta_pp': round(cur_pos - prev_pos, 1),
    }

def get_store_mapping(ch):
    rows = ch.query(f"SELECT branch_name, rbm, bdm FROM branch_master WHERE {RETAIL_BRANCH_FILTER}").result_rows
    ch_dict = {r[0].strip().upper(): {'rbm': r[1], 'bdm': r[2]} for r in rows if r[0]}
    return ch_dict, list(ch_dict.keys())

def apply_filters(request, base_query):
    # Minimal filter logic for brevity; this can be expanded
    rbm_filter = request.GET.get('rbm', 'all')
    bdm_filter = request.GET.get('bdm', 'all')
    store_filter = request.GET.get('store', 'all')
    
    where_clauses = []
    # If store-level filtering is required, we can inject it here
    # For now we'll do global or simple store filtering
    if store_filter != 'all':
        store_safe = store_filter.replace(chr(39), chr(39)+chr(39))
        where_clauses.append(f"store_name = '{store_safe}'")
        
    if where_clauses:
        return f"{base_query} WHERE {' AND '.join(where_clauses)}"
    return base_query

def get_executive_kpis(request):
    ch = get_ch_client()
    q = f"SELECT count(), sum(rating <= 2), sum(rating >= 4) FROM google_reviews{_where(request)}"
    res = ch.query(q).result_rows[0]
    total = res[0]
    negative = res[1] or 0
    positive = res[2] or 0
    
    q_issues = f"SELECT dim_primary_root_cause, count() as cnt FROM google_reviews{_where(request, 'rating <= 2', COMPLAINT_COND)} GROUP BY dim_primary_root_cause ORDER BY cnt DESC LIMIT 1"
    top_issue = ch.query(q_issues).result_rows
    top_issue_text = top_issue[0][0] if top_issue else "N/A"
    
    return {
        'total_reviews': total,
        'negative_rate': round(negative / total * 100, 1) if total > 0 else 0,
        'positive_rate': round(positive / total * 100, 1) if total > 0 else 0,
        'top_issue': top_issue_text,
        'period': get_period_comparison(ch, request),
    }

def get_root_cause_analysis(request):
    ch = get_ch_client()
    q = f"SELECT dim_primary_root_cause, count() as cnt FROM google_reviews{_where(request, 'rating <= 2', COMPLAINT_COND)} GROUP BY dim_primary_root_cause ORDER BY cnt DESC"
    res = ch.query(q).result_rows
    
    labels = [r[0] for r in res]
    data = [r[1] for r in res]
    
    # Pareto calculation
    total_issues = sum(data)
    cumulative = 0
    pareto_data = []
    for d in data:
        cumulative += d
        pareto_data.append(round(cumulative / total_issues * 100, 1) if total_issues > 0 else 0)
        
    return {
        'labels': labels,
        'data': data,
        'pareto': pareto_data
    }

def get_store_performance(request):
    ch = get_ch_client()
    q = apply_filters(request, """
        SELECT store_name, count() as total, sum(rating <= 2) as negative
        FROM google_reviews 
        GROUP BY store_name 
        HAVING total > 20 
        ORDER BY negative / total DESC 
        LIMIT 10
    """)
    res = ch.query(q).result_rows
    
    worst_stores = []
    for r in res:
        worst_stores.append({
            'store': r[0],
            'total': r[1],
            'negative': r[2],
            'rate': round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0
        })
        
    return {'worst_stores': worst_stores}

def get_trends(request):
    ch = get_ch_client()
    # Parse string to actual Date for grouping and sorting
    q = apply_filters(request, """
        SELECT toDate(parseDateTimeBestEffort(replaceRegexpAll(updated_at, ',|Thu|Fri|Sat|Sun|Mon|Tue|Wed', ''))) as parsed_date, 
               sum(rating <= 2) as negative, sum(rating >= 4) as positive
        FROM google_reviews
        GROUP BY parsed_date
        ORDER BY parsed_date DESC
        LIMIT 30
    """)
    res = ch.query(q).result_rows
    # Sort chronological
    res = sorted(res, key=lambda x: x[0])
    
    dates = [str(r[0]) for r in res]
    neg = [r[1] for r in res]
    pos = [r[2] for r in res]
    
    return {
        'dates': dates,
        'negative': neg,
        'positive': pos
    }

def get_reviews(request):
    ch = get_ch_client()
    limit = int(request.GET.get('limit', 50))
    q = apply_filters(request, f"""
        SELECT store_name, updated_at, rating, reviewer_name, comment, 
               dim_product_sentiment, dim_service_sentiment, dim_primary_root_cause, 
               dim_secondary_issue, dim_emotion, dim_suggested_action
        FROM google_reviews
        ORDER BY parseDateTimeBestEffort(replaceRegexpAll(updated_at, ',|Thu|Fri|Sat|Sun|Mon|Tue|Wed', '')) DESC
        LIMIT {limit}
    """)
    res = ch.query(q).result_rows
    
    reviews = []
    for r in res:
        reviews.append({
            'store': r[0],
            'date': r[1],
            'rating': r[2],
            'author': r[3],
            'comment': r[4],
            'product_sentiment': r[5],
            'service_sentiment': r[6],
            'primary_issue': r[7],
            'secondary_issue': r[8],
            'emotion': r[9],
            'action': r[10]
        })
    return {'reviews': reviews}

def get_all_sections(request):
    return {
        'kpis': get_executive_kpis(request),
        'root_cause': get_root_cause_analysis(request),
        'stores': get_store_performance(request),
        'trends': get_trends(request),
        'reviews': get_reviews(request)
    }
