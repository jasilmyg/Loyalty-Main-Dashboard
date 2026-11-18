"""Detailed analysis for each card (and every sub-category inside it) of the legacy
Customer Review RCA dashboard (/rca-dashboard/).

Reviews rated <= 3 come from dashboard.rca_labels (manually reviewed by meaning, falling back to
dashboard.rca_rules.CLS_SQL for any unlabelled/blank rows); 4-5 star reviews come from CLS_SQL.
Both this module and dashboard.rca_logic.build_rca_data share that source, so card and detail counts always match.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib

from django.core.cache import cache

from analytics.clickhouse_service import get_ch_client
from dashboard.rca_logic import _as_date, get_store_mapping, in_date_window, resolve_date_window
from dashboard.rca_rules import CLS_SQL, REVIEWS_SRC
from dashboard.rca_labels import _fix_text, get_low_rows, labels_version
from dashboard.rca_v2_logic import DATE, _pct, _ins


def _q(values):
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


# key -> definition. items = (key, label, rca_subcategories, hf_categories)
CARDS = {
    'after_sales': {
        'title': 'After-Sales & Service Friction', 'category': 'After-Sales & Service Friction', 'complaint': True,
        'hf': ['Delayed Service & Long Waiting Time', 'Product Defect & Hardware Issue',
               'Warranty & Replacement Denial'],
        'items': [
            ('delivery', 'Delivery, Installation & Stock Delays', [], []),
            ('delays', 'Service & Repair Delays', ['Severe Delays & Turnaround Lag'], ['Delayed Service & Long Waiting Time']),
            ('warranty', 'Warranty & Replacements', ['Warranty & Replacement Disputes'], ['Warranty & Replacement Denial']),
            ('unresolved', 'Unresolved / Repeat Faults', ['Unresolved Faults / Defective Repair'], ['Product Defect & Hardware Issue']),
            ('updates', 'Missing Proactive Updates', ['Missing Updates & Proactive Follow-up'], []),
            ('cost', 'Service Charges & Spares', ['Service Charges & Spare Cost Disputes'], []),
            ('general', 'General After-Sales Complaints', ['General After-Sales Grievances'], []),
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
    'sales': {
        'title': 'Sales, Store & General Complaints', 'category': 'Sales, Store & General Complaints', 'complaint': True,
        'hf': ['Pricing & High Cost'],
        'items': [
            ('offers', 'Offers, Pricing & Mis-selling', [], ['Pricing & High Cost']),
            ('store', 'Store Facilities & Waiting', [], []),
            ('general_service', 'General Customer-Service Complaint', [], []),
            ('unspecified', 'Unspecified Complaint (no reason given)', [], []),
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


def _card_base(card_key, where_store, extra=''):
    """SQL sub-select of the 4-5 star reviews of one card with an ``item`` column.
    (Reviews rated 1-3 stars come from the manually reviewed labels, see dashboard.rca_labels.)"""
    cols = "rating, store_name, comment, dim_emotion, dim_suggested_action"
    return (f"(SELECT splitByChar('/', cls)[2] AS item, d, {cols} FROM "
            f"(SELECT {CLS_SQL} AS cls, {DATE} AS d, {cols} FROM {REVIEWS_SRC} WHERE rating >= 4{where_store}{extra}) "
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
    if scond == "0":
        return {'status': 'empty'}
    where_store = f" AND {scond}" if scond else ""
    dw = resolve_date_window(request, ch)
    d_from, d_to, date_sql = dw['date_from'], dw['date_to'], dw['date_sql']
    where_all = f"{where_store}{date_sql}"
    span_days = ((d_to - d_from).days + 1) if (d_from and d_to) else None
    labels = {k: l for k, l, _s, _h in d['items']}
    item_keys = [k for k, *_ in d['items']]

    all_total, maxd = ch.query(
        f"SELECT count(), max({DATE}) FROM {REVIEWS_SRC} WHERE 1 = 1{where_all}").result_rows[0]
    if not all_total or not maxd:
        return {'status': 'empty'}
    maxd = _as_date(maxd)
    d30, d60 = maxd - timedelta(days=30), maxd - timedelta(days=60)

    store_tot = dict(ch.query(f"SELECT store_name, count() FROM {REVIEWS_SRC} WHERE 1 = 1{where_all} GROUP BY store_name").result_rows)

    # ---- aggregates per (item, store): n, sum rating, 1-2*, 3*, 4-5*, last 30d, previous 30d ---------
    G = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    months_n = defaultdict(int)                 # (month, item) -> reviews
    emo, act = defaultdict(Counter), defaultdict(Counter)
    cand = defaultdict(list)                    # item -> [(sort key, sample)]
    prefix = card_key + '/'

    for r in get_low_rows(ch, where_store):     # reviews rated 1-3 stars, manually labelled
        if not r['cls'].startswith(prefix):
            continue
        dt = _as_date(r['d'])
        if (d_from or d_to) and not in_date_window(dt, d_from, d_to):
            continue
        item = r['cls'][len(prefix):]
        g = G[(item, r['store'])]
        rt = r['rating']
        g[0] += 1; g[1] += rt
        g[2] += rt <= 2; g[3] += rt == 3; g[4] += rt >= 4
        if dt:
            g[5] += dt > d30
            g[6] += (dt <= d30 and dt > d60)
            months_n[(dt.replace(day=1), item)] += 1
        if r['emo']: emo[item][r['emo']] += 1
        if r['act']: act[item][r['act']] += 1
        if r['comment'].strip():
            key = (rt, -(dt.toordinal() if dt else 0)) if d['complaint'] else (-(dt.toordinal() if dt else 0),)
            cand[item].append((key, {'store': r['store'], 'rating': rt, 'date': str(dt), 'comment': r['comment'][:280]}))

    if card_key == 'other':                     # + the 4-5 star reviews (SQL rules)
        base = _card_base(card_key, where_store, date_sql)
        md = f"toDate('{maxd}')"
        for item, store, n, sr, neg, neu, pos, l30, p30 in ch.query(f"""
                SELECT item, store_name, count(), sum(rating), countIf(rating <= 2), countIf(rating = 3), countIf(rating >= 4),
                       countIf(d > subtractDays({md}, 30)),
                       countIf(d <= subtractDays({md}, 30) AND d > subtractDays({md}, 60))
                FROM {base} GROUP BY item, store_name""").result_rows:
            g = G[(item, store)]
            for i, v in enumerate((n, int(sr), neg, neu, pos, l30, p30)):
                g[i] += v
        for m, item, c in ch.query(f"SELECT toStartOfMonth(d) AS m, item, count() FROM {base} WHERE d IS NOT NULL GROUP BY m, item").result_rows:
            months_n[(_as_date(m), item)] += c
        for item, e, c in ch.query(f"SELECT item, dim_emotion, count() FROM {base} GROUP BY item, dim_emotion").result_rows:
            if e: emo[item][e] += c
        for item, a, c in ch.query(f"SELECT item, dim_suggested_action, count() FROM {base} GROUP BY item, dim_suggested_action").result_rows:
            if a: act[item][a] += c
        for item, store, rating, comment, dt in ch.query(f"""
                SELECT item, store_name, rating, comment, d FROM {base} WHERE comment != ''
                ORDER BY d DESC LIMIT 3 BY item""").result_rows:
            dt = _as_date(dt)
            cand[item].append(((-(dt.toordinal() if dt else 0),),
                               {'store': store, 'rating': rating, 'date': str(dt), 'comment': _fix_text(comment)[:280]}))

    by_item = defaultdict(dict)
    for (item, store), g in G.items():
        by_item[item][store] = g
    card_total = sum(g[0] for g in G.values())

    months = sorted({m for m, _i in months_n})[-12:]
    series = {k: [0] * len(months) for k in item_keys}
    for (m, item), c in months_n.items():
        if m in months and item in series:
            series[item][months.index(m)] = c

    # --- assemble items -------------------------------------------------------------
    items = []
    for k in item_keys:
        sg = by_item.get(k, {})
        cnt = sum(g[0] for g in sg.values())
        sumr = sum(g[1] for g in sg.values())
        neg = sum(g[2] for g in sg.values()); neu = sum(g[3] for g in sg.values()); pos = sum(g[4] for g in sg.values())
        l30 = sum(g[5] for g in sg.values()); p30 = sum(g[6] for g in sg.values())
        tops = sorted(sg.items(), key=lambda kv: -kv[1][0])[:5]
        samples = [x[1] for x in sorted(cand.get(k, []), key=lambda x: x[0])[:3]]
        items.append({
            'key': k, 'label': labels[k], 'count': cnt, 'share': _pct(cnt, card_total),
            'share_of_all': _pct(cnt, all_total, 2),
            'avg_rating': round(sumr / cnt, 2) if cnt else 0,
            'negative': neg, 'neg_rate': _pct(neg, cnt) if cnt else 0,
            'neutral': neu, 'positive': pos,
            'pos_rate': _pct(pos, cnt) if cnt else 0,
            'last_30d': l30, 'prev_30d': p30,
            'change_pct': (_change(l30, p30) if (cnt and (span_days is None or span_days >= 55)) else None),
            'stores': len(sg),
            'top_emotion': emo[k].most_common(1)[0][0] if emo.get(k) else '—',
            'top_action': act[k].most_common(1)[0][0] if act.get(k) else '—',
            'top_stores': [{'store': st, 'count': g[0], 'share': _pct(g[0], store_tot.get(st, 0))} for st, g in tops],
            'samples': samples,
        })

    # --- RBM / BDM / store breakdown (normalised by each unit's total reviews) ---------
    per_store = defaultdict(lambda: [0, 0])
    for (item, store), g in G.items():
        per_store[store][0] += g[0]; per_store[store][1] += g[2]
    agg = {'rbm': defaultdict(lambda: [0, 0, 0, 0]), 'bdm': defaultdict(lambda: [0, 0, 0, 0])}
    stores = []
    for store, (c, neg) in per_store.items():
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
        'last_30d': cur30, 'prev_30d': prev30,
        'change_pct': (_change(cur30, prev30) if (span_days is None or span_days >= 55) else None),
        'as_of': str(maxd),
    }

    insights = _insights(d, items, summary, groups('rbm'), stores_ranked)
    return {
        'status': 'ok', 'card': card_key, 'title': d['title'], 'complaint': d['complaint'],
        'summary': summary, 'items': items,
        'monthly': {'months': [m.strftime('%b %y') for m in months],
                    'series': [{'key': k, 'label': labels[k], 'data': series[k]} for k in item_keys]},
        'rbms': groups('rbm'), 'bdms': groups('bdm'), 'stores': stores_ranked[:15],
        'insights': insights,
    }


GENERIC_KEYS = {'general', 'unspecified', 'general_service'}


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
        sev = sorted([i for i in items if i['count'] >= 30 and i['key'] not in GENERIC_KEYS], key=lambda i: i['avg_rating'])
        if sev:
            i = sev[0]
            out.append(_ins('danger', 'Most severe sub-category',
                f"“{i['label']}” — lowest average rating ({i['avg_rating']}★) across {i['count']:,} reviews, "
                f"{i['neg_rate']}% of them 1–2★."))
        gen = next((i for i in sorted(items, key=lambda i: -i['count']) if i['key'] in GENERIC_KEYS), None)
        if gen and gen['count'] and gen['share'] >= 30:
            out.append(_ins('info', 'General bucket',
                f"{gen['share']}% of this card ({gen['count']:,}) is a low-rating review with no specific cause in the text "
                f"(often rating-only or very short comments)."))
    elif s['reviews']:
        un = next((i for i in items if i['key'] == 'unclassified'), None)
        out.append(_ins('success', 'Satisfaction level', f"Average rating {s['avg_rating']}★ with {s['pos_rate']}% 4–5★ reviews."))
        if un and un['count']:
            out.append(_ins('info', 'Unclassified share', f"{un['share']}% of this card ({un['count']:,}) has no usable comment text."))
    if s['prev_30d'] and s['change_pct'] is not None:
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
    s = "|".join(
        request.GET.get(k, d)
        for k, d in (('rbm', 'all'), ('bdm', 'all'), ('store', 'all'), ('period', 'all'), ('date_from', ''), ('date_to', ''))
    )
    fh = hashlib.md5(s.encode('utf-8')).hexdigest()[:12]
    key = "rca_legacy_detail_v5:{}:{}:{}".format(labels_version(), card_key, fh)
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = build_card_detail(request, card_key)
    if result is not None and result.get('status') == 'ok':
        cache.set(key, result, 300)
    return result
