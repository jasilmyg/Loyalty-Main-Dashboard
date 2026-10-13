"""Detailed analysis + key insights for each section of the RCA v2 dashboard.

Every builder returns {'insights': [...], 'detail': {...}} and is cached for 5 minutes.
"""
import datetime
import statistics
from collections import defaultdict

from django.core.cache import cache

from analytics.clickhouse_service import get_ch_client
from dashboard.rca_logic import get_store_mapping as get_store_rbm_mapping
from dashboard.rca_v2_logic import (
    DATE, NON_COMPLAINT_SQL, COMPLAINT_COND,
    _where, _pct, _ins, get_period_comparison,
)

NEG = "rating <= 2"
HIGH_PRIORITY = "rating <= 2 AND dim_emotion IN ('Anger', 'Frustration')"
CACHE_SECONDS = 300


def _short(s, n=40):
    return s if len(s) <= n else s[:n - 1] + '…'


def _fmt(n):
    return f"{int(n):,}"


def _month_rows(ch, W):
    rows = ch.query(f"""
        SELECT toStartOfMonth({DATE}) AS m, count(), sum(rating <= 2), sum(rating >= 4), avg(rating)
        FROM google_reviews{W} GROUP BY m ORDER BY m
    """).result_rows
    return [{
        'month': r[0].strftime('%b %Y'), 'total': r[1], 'negative': r[2],
        'neg_rate': _pct(r[2], r[1]), 'pos_rate': _pct(r[3], r[1]),
        'avg_rating': round(float(r[4]), 2),
    } for r in rows if r[0]]


# --------------------------------------------------------------------------- A
def build_overview(request, ch):
    W = _where(request)
    total, neg, pos, avg = ch.query(
        f"SELECT count(), sum({NEG}), sum(rating >= 4), avg(rating) FROM google_reviews{W}").result_rows[0]
    five = ch.query(f"SELECT countIf(rating = 5) FROM google_reviews{W}").result_rows[0][0]
    per = get_period_comparison(ch, request) or {}

    monthly = _month_rows(ch, W)
    if monthly and per:
        # Latest month only covers data up to 'as_of'
        monthly[-1]['month'] += ' (partial)'

    ratings = [{'rating': r[0], 'count': r[1], 'pct': _pct(r[1], total)} for r in ch.query(
        f"SELECT rating, count() FROM google_reviews{W} GROUP BY rating ORDER BY rating").result_rows]

    emotions = [{'emotion': r[0], 'total': r[1], 'negative': r[2]} for r in ch.query(
        f"SELECT dim_emotion, count(), sum({NEG}) FROM google_reviews{W} GROUP BY dim_emotion ORDER BY count() DESC"
    ).result_rows]

    matrix_rows = ch.query(f"""
        SELECT dim_product_sentiment, dim_service_sentiment, count()
        FROM google_reviews{_where(request, NEG)} GROUP BY 1, 2 ORDER BY 3 DESC
    """).result_rows
    matrix = [{'product': r[0], 'service': r[1], 'count': r[2]} for r in matrix_rows]
    neg_total = sum(m['count'] for m in matrix)
    service_neg = sum(m['count'] for m in matrix if m['service'] == 'Negative')
    product_neg = sum(m['count'] for m in matrix if m['product'] == 'Negative')
    both_neg = sum(m['count'] for m in matrix if m['service'] == 'Negative' and m['product'] == 'Negative')

    insights = [
        _ins('info', 'Customer satisfaction baseline',
             f"{_fmt(total)} reviews with an average rating of {avg:.2f}★. "
             f"{_pct(five, total)}% are 5-star and {_pct(pos, total)}% are 4★+."),
        _ins('warning' if _pct(neg, total) > 4 else 'success', 'Negative experience rate',
             f"{_fmt(neg)} reviews ({_pct(neg, total)}%) are 1–2★. Roughly 1 in {round(total / neg) if neg else '–'} customers leaves a negative rating."),
    ]
    if per and per['prev_total']:
        d = per['neg_delta_pp']
        kind = 'danger' if d > 0.3 else ('success' if d < -0.3 else 'info')
        word = 'up' if d > 0 else ('down' if d < 0 else 'flat')
        insights.append(_ins(kind, 'Trend vs previous 30 days',
            f"Negative rate is {word} {abs(d)} pp ({per['neg_rate_prev']}% → {per['neg_rate_cur']}%). "
            f"Review volume changed {per['total_delta_pct']:+}% ({_fmt(per['prev_total'])} → {_fmt(per['cur_total'])}). "
            f"Period ends {per['as_of']}."))
    if neg_total:
        sp, pp = _pct(service_neg, neg_total), _pct(product_neg, neg_total)
        if abs(sp - pp) < 5:
            title = 'Service and product failures are equally common'
        else:
            title = 'Service, not product, drives dissatisfaction' if sp > pp else 'Product, not service, drives dissatisfaction'
        insights.append(_ins('warning', title,
            f"Among negative reviews, service is negative in {sp}% and product in {pp}%. "
            f"{_pct(both_neg, neg_total)}% fail on both, so most unhappy customers are hit by a combined product + service experience."))
    neg_emotions = sorted(emotions, key=lambda e: -e['negative'])
    if neg_emotions and neg:
        e = neg_emotions[0]
        insights.append(_ins('info', 'Dominant emotion in negative reviews',
            f"{e['emotion']} appears in {_pct(e['negative'], neg)}% of negative reviews"
            + (f", followed by {neg_emotions[1]['emotion']} ({_pct(neg_emotions[1]['negative'], neg)}%)." if len(neg_emotions) > 1 else ".")))

    return {'insights': insights, 'detail': {
        'period': per, 'monthly': monthly, 'ratings': ratings, 'emotions': emotions,
        'matrix': matrix, 'summary': {'total': total, 'negative': neg, 'avg_rating': round(float(avg), 2)},
    }}


# --------------------------------------------------------------------------- B
def build_root_cause(request, ch):
    W_neg = _where(request, NEG, COMPLAINT_COND)
    cats = ch.query(f"""
        SELECT dim_primary_root_cause, count(), avg(rating) FROM google_reviews{W_neg}
        GROUP BY 1 ORDER BY 2 DESC""").result_rows
    total_neg_attr = sum(c[1] for c in cats)

    mentions = dict(ch.query(f"""
        SELECT dim_primary_root_cause, count() FROM google_reviews{_where(request, COMPLAINT_COND)} GROUP BY 1
    """).result_rows)
    secondary_all = dict(ch.query(f"""
        SELECT dim_secondary_issue, count() FROM google_reviews{_where(request)} GROUP BY 1
    """).result_rows)

    sec_rows = ch.query(f"""
        SELECT dim_primary_root_cause, dim_secondary_issue, count() FROM google_reviews{W_neg}
        GROUP BY 1, 2 ORDER BY 3 DESC""").result_rows
    secondary = defaultdict(list)
    for p, s, c in sec_rows:
        if s not in ('N/A', p) and len(secondary[p]) < 4:
            secondary[p].append({'issue': s, 'count': c})

    sample_rows = ch.query(f"""
        SELECT dim_primary_root_cause, store_name, rating, comment, {DATE} AS d
        FROM google_reviews{_where(request, NEG, COMPLAINT_COND, "comment != ''")}
        ORDER BY d DESC LIMIT 3 BY dim_primary_root_cause""").result_rows
    samples = defaultdict(list)
    for p, store, rating, comment, d in sample_rows:
        samples[p].append({'store': store, 'rating': rating, 'date': str(d), 'comment': comment[:260]})

    unattributed = ch.query(f"""
        SELECT count() FROM google_reviews{_where(request, NEG, f"dim_primary_root_cause IN ({NON_COMPLAINT_SQL})")}
    """).result_rows[0][0]
    all_neg = ch.query(f"SELECT count() FROM google_reviews{_where(request, NEG)}").result_rows[0][0]

    out, cum = [], 0
    for name, cnt, avg_r in cats:
        cum += cnt
        out.append({
            'category': name, 'negatives': cnt, 'share': _pct(cnt, total_neg_attr),
            'cumulative': _pct(cum, total_neg_attr), 'mentions': mentions.get(name, 0),
            'neg_rate': _pct(cnt, mentions.get(name, 0)), 'avg_rating': round(float(avg_r), 2),
            'secondary': secondary.get(name, []), 'samples': samples.get(name, []),
            'secondary_mentions': secondary_all.get(name, 0),
        })

    insights = []
    if out:
        top3 = out[:3]
        insights.append(_ins('danger', 'Top 3 causes drive most dissatisfaction',
            f"{', '.join(c['category'] for c in top3)} account for {top3[-1]['cumulative']}% of attributable negative reviews."))
        n80 = next((i + 1 for i, c in enumerate(out) if c['cumulative'] >= 80), len(out))
        insights.append(_ins('info', 'Pareto (80/20) view',
            f"Fixing the top {n80} of {len(out)} complaint categories resolves ~80% of negative reviews."))
        severe = sorted([c for c in out if c['mentions'] >= 200], key=lambda c: -c['neg_rate'])
        if severe:
            s = severe[0]
            insights.append(_ins('danger', 'Most severe category',
                f"“{s['category']}” — {s['neg_rate']}% of the {_fmt(s['mentions'])} reviews mentioning it are 1–2★ (avg {s['avg_rating']}★ among negatives)."))
        latent = [(c, c['secondary_mentions'] / c['mentions']) for c in out if c['mentions'] >= 100 and c['secondary_mentions']]
        latent.sort(key=lambda x: -x[1])
        if latent and latent[0][1] >= 2:
            c, ratio = latent[0]
            insights.append(_ins('warning', 'Hidden friction as a secondary issue',
                f"“{c['category']}” is the primary cause in {_fmt(c['mentions'])} reviews but is mentioned as a secondary issue in {_fmt(c['secondary_mentions'])} — "
                f"{ratio:.0f}× more. Many satisfied customers still report it."))
    if unattributed:
        insights.append(_ins('info', 'Low-star reviews without a clear complaint',
            f"{_fmt(unattributed)} of {_fmt(all_neg)} negative reviews ({_pct(unattributed, all_neg)}%) were tagged by the classifier as positive / general inquiry (e.g. short, mixed or sarcastic text) so they are excluded from the ranking. Worth a manual audit."))

    return {'insights': insights, 'detail': {'categories': out, 'unattributed': unattributed, 'all_negative': all_neg}}


# --------------------------------------------------------------------------- C
def _top_complaint(counts):
    names = ['Rude Staff', 'Delayed Service', 'Product / Warranty', 'Phone / Follow-up', 'Pricing']
    if not any(counts):
        return '—'
    return names[max(range(len(counts)), key=lambda i: counts[i])]


def _group_row(name, agg, p0, k=50):
    total, neg = agg['total'], agg['neg']
    cats = agg['cats']
    return {
        'name': name, 'stores': agg['stores'], 'total': total, 'negative': neg,
        'rate': _pct(neg, total), 'adj_rate': round((neg + k * p0) / (total + k) * 100, 2),
        'staff': _pct(cats[0], total), 'delay': _pct(cats[1], total),
        'product': _pct(cats[2], total), 'contact': _pct(cats[3], total),
        'top_complaint': _top_complaint(cats),
    }


def build_stores(request, ch):
    mapping = get_store_rbm_mapping(ch)
    rows = ch.query(f"""
        SELECT store_name, count(), sum({NEG}),
            countIf({NEG} AND dim_primary_root_cause = 'Rude Staff & Bad Behavior'),
            countIf({NEG} AND dim_primary_root_cause = 'Delayed Service & Long Waiting Time'),
            countIf({NEG} AND dim_primary_root_cause IN ('Product Defect & Hardware Issue', 'Warranty & Replacement Denial')),
            countIf({NEG} AND dim_primary_root_cause IN ('No Follow-up or Callback', 'Unanswered Calls')),
            countIf({NEG} AND dim_primary_root_cause = 'Pricing & High Cost')
        FROM google_reviews{_where(request)} GROUP BY store_name
    """).result_rows
    net_total = sum(r[1] for r in rows)
    net_neg = sum(r[2] for r in rows)
    p0 = net_neg / net_total if net_total else 0

    def new_agg():
        return {'stores': 0, 'total': 0, 'neg': 0, 'cats': [0] * 5}

    rbm_aggs, bdm_aggs, stores = defaultdict(new_agg), defaultdict(new_agg), []
    for r in rows:
        name, total, neg, cats = r[0], r[1], r[2], list(r[3:8])
        info = mapping.get(name, {'rbm': 'Unknown', 'bdm': 'Unknown'})
        agg = {'stores': 1, 'total': total, 'neg': neg, 'cats': cats}
        row = _group_row(name, agg, p0)
        row.update({'rbm': info['rbm'], 'bdm': info['bdm']})
        stores.append(row)
        for target, key in ((rbm_aggs, info['rbm']), (bdm_aggs, info['bdm'])):
            a = target[key]
            a['stores'] += 1
            a['total'] += total
            a['neg'] += neg
            a['cats'] = [x + y for x, y in zip(a['cats'], cats)]

    stores.sort(key=lambda s: -s['adj_rate'])
    rbms = sorted((_group_row(k, v, p0) for k, v in rbm_aggs.items()), key=lambda g: -g['adj_rate'])
    bdms = sorted((_group_row(k, v, p0) for k, v in bdm_aggs.items()), key=lambda g: -g['adj_rate'])

    insights = []
    net_rate = _pct(net_neg, net_total)
    ranked = [s for s in stores if s['total'] >= 20]
    raw_sorted = sorted(ranked, key=lambda s: -s['rate'])
    if raw_sorted:
        w = raw_sorted[0]
        insights.append(_ins('danger', 'Highest negative rate',
            f"{w['name']} — {w['rate']}% negative ({w['negative']} of {_fmt(w['total'])} reviews) vs. network average {net_rate}%."))
    if ranked:
        over = [s for s in ranked if s['rate'] >= 2 * net_rate]
        insights.append(_ins('warning', 'Stores at 2× the network average',
            f"{len(over)} of {len(ranked)} stores (≥20 reviews) run at least 2× the network negative rate ({net_rate}%)."))
        top10 = sorted(ranked, key=lambda s: -s['negative'])[:10]
        insights.append(_ins('info', 'Concentration of negative reviews',
            f"The 10 stores with the most negative reviews hold {_pct(sum(s['negative'] for s in top10), net_neg)}% of all negative reviews."))
        adj_top = sorted(ranked, key=lambda s: -s['adj_rate'])[0]
        if raw_sorted and adj_top['name'] != raw_sorted[0]['name']:
            insights.append(_ins('info', 'Size-normalised ranking differs',
                f"After normalising for review volume, {adj_top['name']} ({adj_top['adj_rate']}%) is the weakest store; "
                f"{raw_sorted[0]['name']}'s raw {raw_sorted[0]['rate']}% is partly a small-sample effect ({_fmt(raw_sorted[0]['total'])} reviews)."))
    known_rbm = [g for g in rbms if g['name'] != 'Unknown' and g['total'] >= 200]
    if known_rbm:
        insights.append(_ins('warning', 'RBM comparison',
            f"{known_rbm[0]['name']}'s region has the highest adjusted negative rate ({known_rbm[0]['adj_rate']}%); "
            f"{known_rbm[-1]['name']}'s the lowest ({known_rbm[-1]['adj_rate']}%)."))
    known_bdm = [g for g in bdms if g['name'] != 'Unknown' and g['total'] >= 200]
    if known_bdm:
        insights.append(_ins('warning', 'BDM comparison',
            f"{known_bdm[0]['name']} has the highest adjusted negative rate ({known_bdm[0]['adj_rate']}%), "
            f"top complaint: {known_bdm[0]['top_complaint']}."))

    return {'insights': insights, 'detail': {
        'network': {'total': net_total, 'negative': net_neg, 'rate': net_rate},
        'stores': stores[:30], 'best_stores': sorted(ranked, key=lambda s: s['adj_rate'])[:5],
        'rbms': rbms, 'bdms': bdms,
        'note': 'Adjusted rate shrinks each rate toward the network average (weight = 50 reviews), so small stores are not unfairly penalised.',
    }}


# --------------------------------------------------------------------------- D
def build_trends(request, ch):
    W = _where(request)
    maxd = ch.query(f"SELECT max({DATE}) FROM google_reviews{W}").result_rows[0][0]
    if not maxd:
        return {'insights': [], 'detail': {}}
    base = f"(SELECT {DATE} AS d, rating, dim_primary_root_cause AS cause FROM google_reviews{W})"
    md = f"toDate('{maxd}')"

    daily = [{'date': str(r[0]), 'total': r[1], 'negative': r[2]} for r in ch.query(f"""
        SELECT d, count(), sum({NEG}) FROM {base} WHERE d > subtractDays({md}, 90) GROUP BY d ORDER BY d""").result_rows]
    weekly = [{'week': r[0].strftime('%d %b'), 'total': r[1], 'negative': r[2], 'neg_rate': _pct(r[2], r[1])}
              for r in reversed(ch.query(f"""
        SELECT toStartOfWeek(d, 1) AS w, count(), sum({NEG}) FROM {base} WHERE d IS NOT NULL
        GROUP BY w ORDER BY w DESC LIMIT 13""").result_rows)]
    monthly = _month_rows(ch, W)
    dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    dow = [{'day': dow_names[r[0] - 1], 'total': r[1], 'negative': r[2], 'neg_rate': _pct(r[2], r[1])}
           for r in ch.query(f"""
        SELECT toDayOfWeek(d), count(), sum({NEG}) FROM {base} WHERE d IS NOT NULL GROUP BY 1 ORDER BY 1""").result_rows]

    # Category early-warning: last 30 days vs. the monthly average of the 90 days before
    cat_rows = ch.query(f"""
        SELECT cause, countIf(d > subtractDays({md}, 30)),
               countIf(d <= subtractDays({md}, 30) AND d > subtractDays({md}, 120))
        FROM {base} WHERE {NEG} AND cause NOT IN ({NON_COMPLAINT_SQL}) GROUP BY cause""").result_rows
    spikes = []
    for cause, cur, prev in cat_rows:
        baseline = prev / 3
        ratio = cur / baseline if baseline else (None if not cur else 99)
        if ratio is None or (cur < 5 and prev < 15):
            continue
        status = 'spike' if ratio >= 1.5 and cur >= 10 else ('rising' if ratio >= 1.2 else ('falling' if ratio <= 0.7 else 'stable'))
        spikes.append({'category': cause, 'last_30d': cur, 'baseline_30d': round(baseline, 1),
                       'change_pct': round((ratio - 1) * 100), 'status': status})
    spikes.sort(key=lambda s: -s['change_pct'])

    # Anomalous days: negatives above mean + 2σ of the preceding 28 days
    anomalies = []
    for i in range(14, len(daily)):
        window = [x['negative'] for x in daily[max(0, i - 28):i]]
        mean, sd = statistics.mean(window), statistics.pstdev(window)
        cur = daily[i]['negative']
        if sd and cur >= mean + 2 * sd and cur >= mean + 3:
            anomalies.append({'date': daily[i]['date'], 'negative': cur, 'expected': round(mean, 1),
                              'z': round((cur - mean) / sd, 1)})
    anomalies = [a for a in anomalies if a['date'] > str(maxd - datetime.timedelta(days=30))]

    per = get_period_comparison(ch, request) or {}
    insights = []
    if per and per['prev_total']:
        d = per['neg_delta_pp']
        insights.append(_ins('danger' if d > 0.3 else ('success' if d < -0.3 else 'info'), 'Last 30 days vs previous 30',
            f"Negative reviews: {_fmt(per['prev_neg'])} → {_fmt(per['cur_neg'])}; rate {per['neg_rate_prev']}% → {per['neg_rate_cur']}% ({d:+} pp)."))
    last30 = [x for x in daily if x['date'] > str(maxd - datetime.timedelta(days=30))]
    if last30:
        peak = max(last30, key=lambda x: x['negative'])
        insights.append(_ins('info', 'Peak negative day (last 30 days)',
            f"{peak['date']} had {peak['negative']} negative reviews out of {peak['total']}."))
    flagged = [s for s in spikes if s['status'] == 'spike']
    if flagged:
        insights.append(_ins('danger', 'Early-warning: complaint spikes',
            "; ".join(f"{s['category']} +{s['change_pct']}% ({s['last_30d']} vs ~{s['baseline_30d']}/mo)" for s in flagged[:3])))
    else:
        rising = [s for s in spikes if s['status'] == 'rising']
        insights.append(_ins('success' if not rising else 'warning', 'Early-warning: complaint categories',
            "No category is spiking (≥1.5× baseline)." + (f" Rising: {', '.join(s['category'] for s in rising[:3])}." if rising else "")))
    falling = [s for s in spikes if s['status'] == 'falling']
    if falling:
        insights.append(_ins('success', 'Improving categories',
            "; ".join(f"{s['category']} {s['change_pct']}%" for s in falling[:3]) + " vs 3-month baseline."))
    if anomalies:
        insights.append(_ins('warning', 'Unusual days',
            f"{len(anomalies)} day(s) in the last 30 exceeded normal negative volume (mean+2σ): " + ", ".join(a['date'] for a in anomalies[:4]) + "."))
    if dow:
        worst = max(dow, key=lambda x: x['neg_rate'])
        best = min(dow, key=lambda x: x['neg_rate'])
        insights.append(_ins('info', 'Weekday pattern',
            f"{worst['day']} has the highest negative rate ({worst['neg_rate']}%), {best['day']} the lowest ({best['neg_rate']}%)."))

    return {'insights': insights, 'detail': {
        'as_of': str(maxd), 'daily': daily[-60:], 'weekly': weekly, 'monthly': monthly,
        'weekday': dow, 'category_trends': spikes, 'anomalies': anomalies,
    }}


# --------------------------------------------------------------------------- E
def build_explorer(request, ch):
    W_neg = _where(request, NEG)
    neg_total = ch.query(f"SELECT count() FROM google_reviews{W_neg}").result_rows[0][0]
    actions = [{'action': r[0], 'negatives': r[1], 'share': _pct(r[1], neg_total), 'high_priority': r[2], 'all_reviews': r[3]}
               for r in ch.query(f"""
        SELECT dim_suggested_action, countIf({NEG}), countIf({HIGH_PRIORITY}), count()
        FROM google_reviews{_where(request)} GROUP BY 1 HAVING countIf({NEG}) > 0 ORDER BY 2 DESC""").result_rows]
    emotions = [{'emotion': r[0], 'count': r[1], 'share': _pct(r[1], neg_total)} for r in ch.query(
        f"SELECT dim_emotion, count() FROM google_reviews{W_neg} GROUP BY 1 ORDER BY 2 DESC").result_rows]

    hp_total = ch.query(f"SELECT count() FROM google_reviews{_where(request, HIGH_PRIORITY)}").result_rows[0][0]
    maxd = ch.query(f"SELECT max({DATE}) FROM google_reviews{_where(request)}").result_rows[0][0]
    hp_30 = ch.query(f"""
        SELECT count() FROM (SELECT {DATE} AS d FROM google_reviews{_where(request, HIGH_PRIORITY)})
        WHERE d > subtractDays(toDate('{maxd}'), 30)""").result_rows[0][0] if maxd else 0
    no_text = ch.query(f"""
        SELECT count() FROM google_reviews{_where(request, NEG, "dim_primary_root_cause IN ('Empty/No Comment')")}""").result_rows[0][0]

    queue = [{'store': r[0], 'date': str(r[1]), 'rating': r[2], 'emotion': r[3], 'issue': r[4],
              'comment': r[5][:260], 'action': r[6]} for r in ch.query(f"""
        SELECT store_name, {DATE} AS d, rating, dim_emotion, dim_primary_root_cause, comment, dim_suggested_action
        FROM google_reviews{_where(request, HIGH_PRIORITY, COMPLAINT_COND, "comment != ''")}
        ORDER BY d DESC LIMIT 15""").result_rows]

    insights = []
    if actions:
        top = actions[0]
        insights.append(_ins('warning', 'Top recommended action',
            f"“{top['action']}” applies to {_fmt(top['negatives'])} negative reviews ({top['share']}% of all negative reviews)."))
        if len(actions) > 1:
            insights.append(_ins('info', 'Next priorities',
                "; ".join(f"{a['action']} ({_fmt(a['negatives'])})" for a in actions[1:3]) + "."))
    insights.append(_ins('danger' if hp_30 else 'info', 'High-priority reviews',
        f"{_fmt(hp_total)} negative reviews express anger or frustration; {_fmt(hp_30)} arrived in the last 30 days and need urgent follow-up."))
    tone = [e for e in emotions if e['emotion'] not in ('Joy & Satisfaction', 'Neutral')]
    if tone and neg_total:
        insights.append(_ins('info', 'Emotional tone of negatives',
            ", ".join(f"{e['emotion']} {e['share']}%" for e in tone[:3]) + " of negative reviews."))
    if no_text:
        insights.append(_ins('info', 'Rating-only negatives',
            f"{_fmt(no_text)} negative reviews have no usable comment — a callback is the only way to learn the cause."))
    insights.append(_ins('info', 'Resolution tracking',
        "The data has no resolved/unresolved flag yet. Actions are AI suggestions; add a status field to track closure."))

    return {'insights': insights, 'detail': {
        'actions': actions, 'emotions': emotions, 'queue': queue,
        'high_priority_total': hp_total, 'high_priority_30d': hp_30, 'negative_total': neg_total,
    }}


BUILDERS = {
    'a': build_overview, 'b': build_root_cause, 'c': build_stores,
    'd': build_trends, 'e': build_explorer,
}


def get_section_detail(request, section):
    section = section.lower()
    if section not in BUILDERS:
        return None
    key = f"rca_v2_detail:{section}:{request.GET.get('store', 'all')}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = BUILDERS[section](request, get_ch_client())
    cache.set(key, result, CACHE_SECONDS)
    return result
