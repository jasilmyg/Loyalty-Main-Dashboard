"""Detailed analysis for each card (and every sub-category inside it) of the legacy
Customer Review RCA dashboard (/rca-dashboard/).

Card / sub-category membership comes from dashboard.rca_rules.CLS_SQL (the same rating-aware
classifier used by dashboard.rca_logic.build_rca_data) so the numbers in the detail view always
reconcile with the numbers shown on the cards.
"""
from collections import defaultdict

from django.core.cache import cache

from analytics.clickhouse_service import get_ch_client
from dashboard.rca_logic import get_store_mapping
from dashboard.rca_rules import CLS_SQL
from dashboard.rca_v2_logic import DATE, _pct, _ins


def _q(values):
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


# key -> definition. items = (key, label, rca_subcategories, hf_categories)
CARDS = {
    'after_sales': {
        'title': 'After-Sales & Service Friction', 'category': 'After-Sales & Service Friction', 'complaint': True,
        'hf': ['Delayed Service & Long Waiting Time', 'Product Defect & Hardware Issue',
               'Warranty & Replacement Denial', 'Pricing & High Cost'],
        'items': [
            ('delays', 'Delays & Turnaround Lag', ['Severe Delays & Turnaround Lag'], ['Delayed Service & Long Waiting Time']),
            ('warranty', 'Warranty & Replacements', ['Warranty & Replacement Disputes'], ['Warranty & Replacement Denial']),
            ('unresolved', 'Unresolved / Repeat Faults', ['Unresolved Faults / Defective Repair'], ['Product Defect & Hardware Issue']),
            ('updates', 'Missing Proactive Updates', ['Missing Updates & Proactive Follow-up'], []),
            ('cost', 'Service Cost & Spares', ['Service Charges & Spare Cost Disputes'], ['Pricing & High Cost']),
            ('general', 'General Service Dissatisfaction', ['General After-Sales Grievances'], []),
        ],
    },
    'staff': {
        'title': 'Staff Interaction & Attitude', 'category': 'Staff Interaction & Attitude', 'complaint': True,
        'hf': ['Rude Staff & Bad Behavior'],
        'items': [
            ('unprofessional', 'Unprofessionalism / Knowledge Gap', ['Unprofessionalism & Knowledge Gaps'], []),
            ('rude', 'Rude Behavior & Talking Style', ['Rude & Arrogant Behavior / Disrespect'], ['Rude Staff & Bad Behavior']),
            ('neglect', 'Inattention & Walk-in Neglect', ['Inattention & Walk-in Neglect'], []),
            ('bias', 'Poor Attitude & Budget Bias', ['Poor Attitude & Budget Bias'], []),
            ('general', 'General Staff Grievances', ['General Staff Dissatisfaction'], []),
        ],
    },
    'communication': {
        'title': 'Communication Breakdown', 'category': 'Communication Breakdown', 'complaint': True,
        'hf': ['No Follow-up or Callback', 'Unanswered Calls'],
        'items': [
            ('chasing', 'Customer Chasing (Repeated Calls)', ['Customer Follow-up Fatigue (Chasing)'], []),
            ('promises', 'Broken Promises & No Callbacks', ['Broken Promises & No Callbacks'], ['No Follow-up or Callback']),
            ('rude_phone', 'Rude Telephonic Response', ['Rude Telephonic Response & Tone'], []),
            ('unanswered', 'Unanswered / Disconnected Calls', ['Unanswered Calls, Busy & Disconnected'], ['Unanswered Calls']),
            ('zero_contact', 'Zero Contact (Phone / WhatsApp)', ['General Zero-Response (Phone/WhatsApp)'], []),
        ],
    },
    'other': {
        'title': 'Positive & General Feedback', 'category': 'Positive Experience & Satisfaction', 'complaint': False,
        'hf': ['Positive Feedback & Good Service', 'Store Ambiance & Cleanliness', 'Empty/No Comment', ''],
        'items': [
            ('praise', 'General Praise & Appreciation', ['General Praise & Appreciation'], []),
            ('ambiance', 'Store Ambiance & Cleanliness', ['Store Ambiance & Cleanliness'], ['Store Ambiance & Cleanliness']),
            ('service', 'Excellent Service & Staff', ['Excellent Service & Staff'], ['Positive Feedback & Good Service']),
            ('product', 'Product Satisfaction & Variety', ['Product Satisfaction & Variety'], []),
            ('unclassified', 'Misc. / Unclassified', [], ['Empty/No Comment', '']),
        ],
    },
}


def _card_base(card_key, where_store):
    """Sub-select of this card's reviews with an ``item`` column (sub-category key)."""
    cols = "rating, store_name, comment, dim_emotion, dim_suggested_action"
    return (f"(SELECT splitByChar('/', cls)[2] AS item, d, {cols} FROM "
            f"(SELECT {CLS_SQL} AS cls, {DATE} AS d, {cols} FROM google_reviews WHERE 1 = 1{where_store}) "
            f"WHERE startsWith(cls, '{card_key}/'))")


def _store_cond(request, mapping):
    rbm, bdm, store = (request.GET.get(k, 'all') for k in ('rbm', 'bdm', 'store'))
    if rbm == 'all' and bdm == 'all' and store == 'all':
        return None, None
    valid = [s for s, i in mapping.items()
             if (rbm == 'all' or i['rbm'] == rbm) and (bdm == 'all' or i['bdm'] == bdm) and (store == 'all' or s == store)]
    if not valid:
        return "0", []
    return f"store_name IN ({_q(valid)})", valid


def _change(cur, prev):
    return round((cur - prev) / prev * 100) if prev else (None if not cur else 100)


def build_card_detail(request, card_key):
    d = CARDS.get(card_key)
    if not d:
        return None
    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    scond, valid = _store_cond(request, mapping)
    where_store = f" AND {scond}" if scond else ""
    labels = {k: l for k, l, _s, _h in d['items']}
    labels['other_items'] = 'Other sub-categories'

    all_total, maxd = ch.query(
        f"SELECT count(), max({DATE}) FROM google_reviews WHERE 1 = 1{where_store}").result_rows[0]
    if not all_total or not maxd:
        return {'status': 'empty'}
    md = f"toDate('{maxd}')"
    base = _card_base(card_key, where_store)

    rows = ch.query(f"""
        SELECT item, count(), avg(rating), countIf(rating <= 2), countIf(rating = 3), countIf(rating >= 4),
               countIf(d > subtractDays({md}, 30)),
               countIf(d <= subtractDays({md}, 30) AND d > subtractDays({md}, 60)),
               uniqExact(store_name), topK(1)(dim_emotion)[1], topK(1)(dim_suggested_action)[1]
        FROM {base} GROUP BY item""").result_rows
    by_item = {r[0]: r for r in rows}
    card_total = sum(r[1] for r in rows)

    store_tot = dict(ch.query(f"SELECT store_name, count() FROM google_reviews WHERE 1 = 1{where_store} GROUP BY store_name").result_rows)

    top_store_rows = ch.query(f"""
        SELECT item, store_name, count() FROM {base} GROUP BY item, store_name
        ORDER BY count() DESC LIMIT 5 BY item""").result_rows
    top_stores = defaultdict(list)
    for item, store, c in top_store_rows:
        top_stores[item].append({'store': store, 'count': c, 'share': _pct(c, store_tot.get(store, 0))})

    order = "rating ASC, d DESC" if d['complaint'] else "d DESC"
    sample_rows = ch.query(f"""
        SELECT item, store_name, rating, comment, d FROM {base} WHERE comment != ''
        ORDER BY {order} LIMIT 3 BY item""").result_rows
    samples = defaultdict(list)
    for item, store, rating, comment, dt in sample_rows:
        samples[item].append({'store': store, 'rating': rating, 'date': str(dt), 'comment': comment[:280]})

    month_rows = ch.query(f"""
        SELECT toStartOfMonth(d) AS m, item, count() FROM {base} WHERE d IS NOT NULL
        GROUP BY m, item ORDER BY m""").result_rows
    months = sorted({r[0] for r in month_rows})[-12:]
    series = {k: [0] * len(months) for k in labels}
    for m, item, c in month_rows:
        if m in months:
            series[item][months.index(m)] = c

    store_rows = ch.query(f"""
        SELECT store_name, count(), countIf(rating <= 2) FROM {base} GROUP BY store_name""").result_rows

    # --- assemble items -------------------------------------------------------------
    items = []
    keys = [k for k, *_ in d['items']] + (['other_items'] if 'other_items' in by_item else [])
    for k in keys:
        r = by_item.get(k)
        cnt = r[1] if r else 0
        items.append({
            'key': k, 'label': labels[k], 'count': cnt, 'share': _pct(cnt, card_total),
            'share_of_all': _pct(cnt, all_total, 2),
            'avg_rating': round(float(r[2]), 2) if r else 0,
            'negative': r[3] if r else 0, 'neg_rate': _pct(r[3], cnt) if r else 0,
            'neutral': r[4] if r else 0, 'positive': r[5] if r else 0,
            'pos_rate': _pct(r[5], cnt) if r else 0,
            'last_30d': r[6] if r else 0, 'prev_30d': r[7] if r else 0,
            'change_pct': _change(r[6], r[7]) if r else None,
            'stores': r[8] if r else 0,
            'top_emotion': r[9] if r else '—', 'top_action': r[10] if r else '—',
            'top_stores': top_stores.get(k, []), 'samples': samples.get(k, []),
        })

    # --- RBM / BDM / store breakdown (normalised by each unit's total reviews) ---------
    agg = {'rbm': defaultdict(lambda: [0, 0, 0, 0]), 'bdm': defaultdict(lambda: [0, 0, 0, 0])}
    stores = []
    for store, c, neg in store_rows:
        info = mapping.get(store, {'rbm': 'Unknown', 'bdm': 'Unknown'})
        tot = store_tot.get(store, 0)
        stores.append({'store': store, 'rbm': info['rbm'], 'bdm': info['bdm'], 'count': c, 'negative': neg,
                       'total': tot, 'rate': _pct(c, tot)})
        for kind in ('rbm', 'bdm'):
            a = agg[kind][info[kind]]
            a[0] += 1; a[1] += tot; a[2] += c; a[3] += neg
    def groups(kind):
        out = [{'name': n, 'stores': a[0], 'total': a[1], 'count': a[2], 'negative': a[3], 'rate': _pct(a[2], a[1])}
               for n, a in agg[kind].items()]
        return sorted(out, key=lambda g: -g['rate'])
    stores_ranked = sorted([s for s in stores if s['total'] >= 50], key=lambda s: -s['rate'])

    cur30 = sum(i['last_30d'] for i in items); prev30 = sum(i['prev_30d'] for i in items)
    neg = sum(i['negative'] for i in items); pos = sum(i['positive'] for i in items)
    avg = (sum(i['avg_rating'] * i['count'] for i in items) / card_total) if card_total else 0
    summary = {
        'reviews': card_total, 'share_of_all': _pct(card_total, all_total), 'all_reviews': all_total,
        'avg_rating': round(avg, 2), 'negative': neg, 'neg_rate': _pct(neg, card_total),
        'positive': pos, 'pos_rate': _pct(pos, card_total),
        'last_30d': cur30, 'prev_30d': prev30, 'change_pct': _change(cur30, prev30), 'as_of': str(maxd),
    }

    insights = _insights(d, items, summary, groups('rbm'), stores_ranked)
    return {
        'status': 'ok', 'card': card_key, 'title': d['title'], 'complaint': d['complaint'],
        'summary': summary, 'items': items,
        'monthly': {'months': [m.strftime('%b %y') for m in months],
                    'series': [{'key': k, 'label': labels[k], 'data': series[k]} for k in keys]},
        'rbms': groups('rbm'), 'bdms': groups('bdm'), 'stores': stores_ranked[:15],
        'insights': insights,
    }


def _insights(d, items, s, rbms, stores):
    out = []
    ranked = sorted(items, key=lambda i: -i['count'])
    top = ranked[0] if ranked else None
    out.append(_ins('info', 'Card size',
        f"{s['reviews']:,} reviews fall under “{d['title']}” — {s['share_of_all']}% of all {s['all_reviews']:,} reviews, spread across {len(items)} sub-categories."))
    if top and s['reviews']:
        out.append(_ins('warning' if top['share'] >= 50 else 'info', 'Largest sub-category',
            f"“{top['label']}” holds {top['share']}% of this card ({top['count']:,} reviews, avg {top['avg_rating']}★)."))
    if d['complaint'] and s['reviews']:
        kind = 'danger' if s['neg_rate'] >= 70 else 'warning'
        out.append(_ins(kind, 'Severity mix',
            f"All {s['reviews']:,} reviews here are genuine complaints: {s['negative']:,} ({s['neg_rate']}%) are 1–2★ and "
            f"{s['reviews'] - s['negative'] - s['positive']:,} are 3★ with an explicit complaint. Average rating {s['avg_rating']}★."))
        sev = sorted([i for i in items if i['count'] >= 30 and i['key'] != 'general'], key=lambda i: i['avg_rating'])
        if sev:
            i = sev[0]
            out.append(_ins('danger', 'Most severe sub-category',
                f"“{i['label']}” — lowest average rating ({i['avg_rating']}★) across {i['count']:,} reviews, "
                f"{i['neg_rate']}% of them 1–2★."))
        gen = next((i for i in items if i['key'] == 'general'), None)
        if gen and gen['count'] and gen['share'] >= 30:
            out.append(_ins('info', 'General bucket',
                f"{gen['share']}% of this card ({gen['count']:,}) is a low-rating review with no specific cause in the text "
                f"(often rating-only or very short comments)."))
    elif s['reviews']:
        un = next((i for i in items if i['key'] == 'unclassified'), None)
        out.append(_ins('success', 'Satisfaction level', f"Average rating {s['avg_rating']}★ with {s['pos_rate']}% 4–5★ reviews."))
        if un and un['count']:
            out.append(_ins('info', 'Unclassified share', f"{un['share']}% of this card ({un['count']:,}) has no usable comment text."))
    if s['prev_30d']:
        ch_ = s['change_pct']
        good = (ch_ < 0) if d['complaint'] else (ch_ > 0)
        out.append(_ins('success' if good else 'warning', 'Last 30 days vs previous 30',
            f"{s['prev_30d']:,} → {s['last_30d']:,} reviews ({ch_:+}%) — as of {s['as_of']}."))
    rising = [i for i in items if i['change_pct'] is not None and i['change_pct'] >= 30 and i['last_30d'] >= 10]
    if rising and d['complaint']:
        out.append(_ins('danger', 'Rising sub-categories',
            "; ".join(f"{i['label']} {i['change_pct']:+}% ({i['prev_30d']}→{i['last_30d']})" for i in rising[:3]) + "."))
    if stores:
        w = stores[0]
        out.append(_ins('warning' if d['complaint'] else 'success', 'Store with the highest share',
            f"{w['store']}: {w['rate']}% of its {w['total']:,} reviews fall in this card (network average {s['share_of_all']}%)."))
    known = [g for g in rbms if g['name'] != 'Unknown' and g['total'] >= 200]
    if known:
        out.append(_ins('info', 'Region comparison (normalised by volume)',
            f"{known[0]['name']}'s region has the highest rate ({known[0]['rate']}%), {known[-1]['name']}'s the lowest ({known[-1]['rate']}%)."))
    return out


def get_card_detail(request, card_key):
    key = "rca_legacy_detail:{}:{}:{}:{}".format(
        card_key, request.GET.get('rbm', 'all'), request.GET.get('bdm', 'all'), request.GET.get('store', 'all'))
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = build_card_detail(request, card_key)
    if result is not None and result.get('status') == 'ok':
        cache.set(key, result, 300)
    return result
