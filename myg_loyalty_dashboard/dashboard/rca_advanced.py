"""Advanced analytics, cross-sell/invoice joins, product & compliance watchlists,
interactive review explorer, and Excel export for the Customer Review RCA dashboard.

Every complaint classification uses ``dashboard.rca_labels.get_low_rows`` (meaning-labelled
1-3★ reviews) merged with ``dashboard.rca_rules.CLS_SQL`` (4-5★ reviews) and
``dashboard.rca_logic.get_store_mapping`` so all numbers reconcile 100% with the main cards.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import io
import re

from django.core.cache import cache

from analytics.clickhouse_service import CH_EXCLUDE_FILTER, get_ch_client
from dashboard.rca_labels import get_low_rows, labels_version
from dashboard.rca_legacy_detail import CARDS, _as_date, _change, _q
from dashboard.rca_logic import get_store_mapping, in_date_window, resolve_date_window
from dashboard.rca_rules import CLS_SQL, REVIEWS_SRC
from dashboard.rca_v2_logic import DATE, _pct
from dashboard.utils import RETAIL_BRANCH_FILTER

COMPLAINT_CARDS = ('after_sales', 'staff', 'communication', 'sales')
CARD_TITLES = {k: CARDS[k]['title'] for k in CARDS}
ITEM_LABELS = {
    f'{c}/{k}': lbl
    for c, cfg in CARDS.items()
    for k, lbl, _s, _h in cfg['items']
}
ITEM_SHORT = {
    'after_sales/delivery': 'Delivery & Install',
    'after_sales/delays': 'Service Delays',
    'after_sales/warranty': 'Warranty & Replace',
    'after_sales/unresolved': 'Unresolved Faults',
    'after_sales/updates': 'Missing Updates',
    'after_sales/cost': 'Service Charges',
    'after_sales/general': 'After-Sales Gen.',
    'staff/unprofessional': 'Skill / Knowledge',
    'staff/rude': 'Rude Staff',
    'staff/neglect': 'Walk-in Neglect',
    'staff/bias': 'Attitude / Bias',
    'staff/general': 'Staff General',
    'communication/chasing': 'Follow-up Chasing',
    'communication/promises': 'Broken Promises',
    'communication/rude_phone': 'Rude on Phone',
    'communication/unanswered': 'Unanswered Calls',
    'communication/zero_contact': 'Zero Response',
    'sales/offers': 'Offers & Mis-selling',
    'sales/store': 'Store & Counter',
    'sales/general_service': 'Gen. Cust. Service',
    'sales/unspecified': 'Unspecified',
}

# 8 balanced heatmap columns covering 100% of complaints
HEATMAP_BUCKETS = [
    ('delivery', 'Delivery & Install', {'after_sales/delivery'}),
    ('faults_warranty', 'Faults & Warranty', {
        'after_sales/unresolved', 'after_sales/warranty', 'after_sales/delays',
        'after_sales/cost', 'after_sales/updates', 'after_sales/general',
    }),
    ('staff_rude', 'Rude & Attitude', {'staff/rude', 'staff/bias'}),
    ('staff_neglect', 'Walk-in Neglect', {'staff/neglect'}),
    ('staff_skill', 'Skill & Staff Gen.', {'staff/unprofessional', 'staff/general'}),
    ('comm', 'Comm / Calls', {
        'communication/chasing', 'communication/promises', 'communication/rude_phone',
        'communication/unanswered', 'communication/zero_contact',
    }),
    ('offers', 'Offers & Mis-selling', {'sales/offers'}),
    ('store_gen', 'Store & General', {'sales/store', 'sales/general_service', 'sales/unspecified'}),
]
_CLS_TO_HBUCKET = {cls: key for key, _lbl, clss in HEATMAP_BUCKETS for cls in clss}

# ---------------------------------------------------------------- Product / Brand / Compliance extractors
PRODUCT_PATTERNS = [
    ('Phone / Mobile', re.compile(r'\b(phone|mobile|smartphone|iphone|android|redmi|xiaomi|vivo|oppo|realme|oneplus|pixel|iqoo|poco|nord|galaxy\s+[amsfz]\d+|screen\s*guard|tempered\s*glass)\b', re.I)),
    ('Air Conditioner', re.compile(r'\b(ac|a\.?c\.?|air\s*condition(?:er|ing)?|split\s*ac|inverter\s*ac|stabilizer|stabiliser)\b', re.I)),
    ('Television', re.compile(r'\b(tv|television|led\s*tv|smart\s*tv|google\s*tv|qled|oled|mini\s*led|bravia)\b', re.I)),
    ('Washing Machine', re.compile(r'\b(washing\s*machine|washer|top\s*load|front\s*load)\b', re.I)),
    ('Refrigerator', re.compile(r'\b(fridge|refrigerator|freezer|double\s*door|single\s*door|side\s*by\s*side)\b', re.I)),
    ('Laptop / PC', re.compile(r'\b(laptop|macbook|ipad|tablet|tab\b|desktop|pc\b|printer|keyboard|mouse|ssd|hard\s*disk|computer|monitor)\b', re.I)),
    ('Kitchen & Small Appl', re.compile(r'\b(mixie|mixer|grinder|blender|cooker|induction|oven|microwave|dishwasher|air\s*fryer|kettle|geyser|heater|water\s*purifier|filter|iron|fan|cooler|vacuum|chimney|hob|gas\s*stove|hair\s*dryer)\b', re.I)),
    ('Audio & Accessories', re.compile(r'\b(earbuds?|airpods?|earphone|headphone|speaker|party\s*box|soundbar|watch|smartwatch|charger|adapter|power\s*bank|cable)\b', re.I)),
]

BRAND_PATTERNS = [
    ('LG', re.compile(r'\blg\b', re.I)),
    ('Samsung', re.compile(r'\bsamsung\b', re.I)),
    ('Apple / iPhone', re.compile(r'\b(apple|iphone|ipad|macbook|airpods?)\b', re.I)),
    ('Haier', re.compile(r'\b(haier|heair)\b', re.I)),
    ('Bosch', re.compile(r'\b(bosch|bosh)\b', re.I)),
    ('IFB', re.compile(r'\bifb\b', re.I)),
    ('Whirlpool', re.compile(r'\b(whirlpool|whirpool)\b', re.I)),
    ('Godrej', re.compile(r'\b(godrej|godreg)\b', re.I)),
    ('Voltas', re.compile(r'\bvoltas\b', re.I)),
    ('Lloyd', re.compile(r'\blloyd\b', re.I)),
    ('Daikin', re.compile(r'\bdaikin\b', re.I)),
    ('Blue Star', re.compile(r'\bblue\s*star\b', re.I)),
    ('Panasonic', re.compile(r'\bpanasonic\b', re.I)),
    ('Sony', re.compile(r'\bsony\b', re.I)),
    ('TCL', re.compile(r'\btcl\b', re.I)),
    ('Vivo', re.compile(r'\bvivo\b', re.I)),
    ('Oppo', re.compile(r'\boppo\b', re.I)),
    ('Xiaomi / Redmi', re.compile(r'\b(xiaomi|redmi|poco)\b', re.I)),
    ('Realme', re.compile(r'\brealme\b', re.I)),
    ('OnePlus', re.compile(r'\bone\s*plus\b', re.I)),
    ('Google Pixel', re.compile(r'\bpixel\b', re.I)),
    ('Motorola', re.compile(r'\b(motorola|moto)\b', re.I)),
    ('HP', re.compile(r'\bhp\b', re.I)),
    ('Lenovo', re.compile(r'\blenovo\b', re.I)),
    ('Dell', re.compile(r'\bdell\b', re.I)),
    ('Acer', re.compile(r'\bacer\b', re.I)),
    ('Asus', re.compile(r'\basus\b', re.I)),
    ('Impex', re.compile(r'\b(impex|inbex)\b', re.I)),
    ('Preethi', re.compile(r'\bpreethi\b', re.I)),
    ('Prestige', re.compile(r'\bprestige\b', re.I)),
    ('Butterfly', re.compile(r'\bbutterfly\b', re.I)),
    ('Havells', re.compile(r'\bhavells\b', re.I)),
    ('Usha', re.compile(r'\busha\b', re.I)),
    ('boAt', re.compile(r'\bboat\b', re.I)),
    ('JBL', re.compile(r'\bjbl\b', re.I)),
]

RISK_DEFS = [
    ('finance_emi', 'Finance / EMI / NBFC Friction', 'warning',
     'Primary EMI, Bajaj Finance, HDB, IDFC, finance charge, loan or credit card disputes.',
     re.compile(
         r'\b(emi|hdb|idfc|finance|finserv|loan|cibil|credit\s*card|'
         r'bajaj(?!\s+(?:mix\w*|grinder|military|fan|cooler|iron|geyser|heater|kettle|cooker|induction|bulb|juicer|blender|roaster|oven)))\b',
         re.I,
     )),
    ('legal_fraud', 'Legal / Fraud / Escalation Threat', 'danger',
     'Mentions consumer court, police, legal action, protest, fraud, cheat or scam.',
     re.compile(r'\b(consumer\s*court|police|legal(?:ly)?|protest|fraud|fraudster|cheat(?:ed|ing|ers)?|scam(?:mers?)?)\b', re.I)),
    ('demo_old', 'Old / Demo / Repacked Sold as New', 'danger',
     'Alleges an old model, display/demo unit, used, open-box, repacked or refurbished product.',
     re.compile(r'\b(old\s*model|used\s+(?:product|item|one|phone|fridge|tv|washing|dishwasher|laptop|ac)|demo\s*piece|display\s*piece|open\s*box|repacked|second\s*hand|refurbished|duplicate|substand[ae]rd)\b', re.I)),
    ('unreq_addon', 'Unrequested Insurance / Add-ons', 'warning',
     'Insurance, warranty or accessories allegedly added to the bill without consent.',
     re.compile(
         r'\b(?:insurance|unrequested|'
         r'(?:added|charged|included|billed|warranty|gdot|protect(?:ion)?|plan|accessor\w*).{0,35}without\s+(?:asking|consent|permission|informing)|'
         r'without\s+(?:asking|consent|permission|informing).{0,35}(?:added|charged|included|billed|warranty|plan|protect|extra\s+(?:money|charge|fee))|'
         r'added\s+(?:in\s+the\s+bill|without\s+consent|extra\s+(?:charge|fee)))\b',
         re.I,
     )),
    ('fake_review', 'Staff Self-Review on Customer Phone', 'danger',
     'Customer reports staff taking their phone to post a 5★ review themselves.',
     re.compile(r'(review\s+themselves|gave\s+themselves|filled\s+it\s+in\s+themselves|wrote\s+the\s+review|typed\s+an?\s+.{0,15}review|asked\s+for\s+(?:our|the|my)\s+phone.{0,80}review)', re.I)),
    ('cross_store', 'Cross-Store Repeated Complaint', 'info',
     'Identical long complaint text posted across multiple stores or reviewers.',
     None),
]
RISK_META = {k: (lbl, sev, desc) for k, lbl, sev, desc, _ in RISK_DEFS}
_RISK_PATS = {k: pat for k, _lbl, _sev, _desc, pat in RISK_DEFS if pat is not None}
_LEGAL_ESCALATION_RE = re.compile(r'\b(consumer\s*court|police|legal(?:ly)?|protest)\b', re.I)


def _assign_single_risk(cm, is_cross_store):
    """Assign each complaint to AT MOST ONE primary Watchlist card by root-cause specificity,
    so no review is double-counted or shown across multiple cards."""
    if _RISK_PATS['fake_review'].search(cm):
        return ['fake_review']
    if _RISK_PATS['demo_old'].search(cm):
        return ['demo_old']
    if _RISK_PATS['unreq_addon'].search(cm):
        return ['unreq_addon']
    if _LEGAL_ESCALATION_RE.search(cm):
        return ['legal_fraud']
    if _RISK_PATS['finance_emi'].search(cm):
        return ['finance_emi']
    if _RISK_PATS['legal_fraud'].search(cm):
        return ['legal_fraud']
    if is_cross_store:
        return ['cross_store']
    return []


def _filter_hash(request):
    s = "|".join(
        request.GET.get(k, d)
        for k, d in (('rbm', 'all'), ('bdm', 'all'), ('store', 'all'), ('period', 'all'), ('date_from', ''), ('date_to', ''))
    )
    return hashlib.md5(s.encode('utf-8')).hexdigest()[:12]


def _resolve_scope(request, mapping):
    rbm, bdm, store = (request.GET.get(k, 'all') for k in ('rbm', 'bdm', 'store'))
    if rbm == 'all' and bdm == 'all' and store == 'all':
        return '', None, (rbm, bdm, store)
    valid = [s for s, i in mapping.items()
             if (rbm == 'all' or i['rbm'] == rbm)
             and (bdm == 'all' or i['bdm'] == bdm)
             and (store == 'all' or s == store)]
    if not valid:
        return ' AND 0', [], (rbm, bdm, store)
    return f" AND store_name IN ({_q(valid)})", valid, (rbm, bdm, store)


def _get_invoice_stats(ch, min_d, max_d):
    """Bills & revenue per branch_master.code + retail branch roster over the review date window."""
    ck = f"rca_inv_stats_v2:{min_d}:{max_d}"
    cached = cache.get(ck)
    if cached is not None:
        return cached
    bm_rows = ch.query(
        f"SELECT upper(trim(code)), upper(trim(branch_name)), rbm, bdm "
        f"FROM branch_master WHERE {RETAIL_BRANCH_FILTER}"
    ).result_rows
    retail_codes = {}
    for code, bname, rbm, bdm in bm_rows:
        if code:
            retail_codes[code] = {'branch_name': bname or code, 'rbm': rbm or 'Unknown', 'bdm': bdm or 'Unknown'}
    inv_rows = ch.query(
        f"SELECT upper(trim(branch)) AS b, count() AS bills, round(sum(invoice_total)) AS rev "
        f"FROM azure_invoice_report "
        f"WHERE {CH_EXCLUDE_FILTER} AND toDate(date) BETWEEN '{min_d}' AND '{max_d}' "
        f"GROUP BY b"
    ).result_rows
    by_code = {b: {'bills': int(bills), 'rev': float(rev or 0)} for b, bills, rev in inv_rows if b}
    out = {'retail_codes': retail_codes, 'by_code': by_code}
    cache.set(ck, out, 3600)
    return out


def _get_store_monthly_stats(ch, where_store, date_sql=''):
    """Total reviews, monthly counts, and 30d/60d/90d review totals per store (SQL over REVIEWS_SRC)."""
    where_full = f"{where_store}{date_sql}"
    fh = hashlib.md5(where_full.encode('utf-8')).hexdigest()[:10]
    ck = f"rca_adv_store_stats_v2:{fh}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    min_d, max_d, total_all = ch.query(
        f"SELECT min({DATE}), max({DATE}), count() FROM {REVIEWS_SRC} WHERE 1 = 1{where_full}"
    ).result_rows[0]
    if not total_all or not max_d:
        return None
    min_d, max_d = _as_date(min_d), _as_date(max_d)
    md = f"toDate('{max_d}')"

    # Total reviews + 30d + baseline (31..90d) per store (denominator for rates & spike radar)
    store_rows = ch.query(f"""
        SELECT store_name, count(),
               countIf(d > subtractDays({md}, 30)),
               countIf(d <= subtractDays({md}, 30) AND d > subtractDays({md}, 60)),
               countIf(d <= subtractDays({md}, 30) AND d > subtractDays({md}, 90))
        FROM (SELECT store_name, {DATE} AS d FROM {REVIEWS_SRC} WHERE 1 = 1{where_full})
        GROUP BY store_name
    """).result_rows
    by_store = {
        st: {'total': int(t), 'r30': int(r30), 'r_prev30': int(rp30), 'r_base60': int(rb60)}
        for st, t, r30, rp30, rb60 in store_rows
    }

    # Monthly total reviews (all ratings) for the 12-month rate toggle
    m_rows = ch.query(f"""
        SELECT toStartOfMonth(d) AS m, count()
        FROM (SELECT {DATE} AS d FROM {REVIEWS_SRC} WHERE 1 = 1{where_full})
        WHERE d IS NOT NULL GROUP BY m ORDER BY m
    """).result_rows
    monthly_all = {_as_date(m): int(cnt) for m, cnt in m_rows if m is not None}

    out = {'min_d': min_d, 'max_d': max_d, 'total_all': int(total_all),
           'by_store': by_store, 'monthly_all': monthly_all}
    cache.set(ck, out, 300)
    return out


def _get_enriched_complaints(ch, where_store, mapping):
    """All complaint rows (cls not starting with 'other/') enriched with store metadata,
    products, brands, and compliance risk tags. Cached 5 min."""
    fh = hashlib.md5(where_store.encode('utf-8')).hexdigest()[:10]
    ck = f"rca_adv_complaints_v3:{labels_version()}:{fh}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    # Detect cross-store repeated long complaints across the whole network
    low_rows = get_low_rows(ch, where_store)
    text_stores = defaultdict(set)
    for r in low_rows:
        if r['cls'].startswith('other/'):
            continue
        cm = r['comment'].strip()
        if len(cm) >= 80:
            text_stores[re.sub(r'\s+', ' ', cm).lower()].add(r['store'])
    dup_texts = {t for t, sts in text_stores.items() if len(sts) >= 2}

    out = []
    for r in low_rows:
        cls = r['cls']
        if cls.startswith('other/'):
            continue
        card, item = cls.split('/', 1)
        cm = r['comment']
        cm_l = cm.lower()
        products = [name for name, pat in PRODUCT_PATTERNS if pat.search(cm)]
        brands = [name for name, pat in BRAND_PATTERNS if pat.search(cm)]
        is_cross = len(cm.strip()) >= 80 and re.sub(r'\s+', ' ', cm.strip()).lower() in dup_texts
        risks = _assign_single_risk(cm, is_cross)
        info = mapping.get(r['store'], {'rbm': 'Unknown', 'bdm': 'Unknown', 'matched_branch': None, 'code': None})
        dt = _as_date(r['d'])
        out.append({
            'store': r['store'],
            'rbm': info['rbm'],
            'bdm': info['bdm'],
            'matched_branch': info.get('matched_branch'),
            'code': info.get('code'),
            'rating': int(r['rating']),
            'date': dt,
            'date_str': str(dt) if dt else '',
            'card': card,
            'item': item,
            'cls': cls,
            'label': ITEM_LABELS.get(cls, cls),
            'comment': cm,
            'comment_l': cm_l,
            'products': products,
            'brands': brands,
            'risks': risks,
        })
    cache.set(ck, out, 300)
    return out


def _bills_for_scope(inv_stats, scope_filters, mapping, valid_stores):
    """Exact invoice bill count without double counting shared branch_master codes."""
    rbm, bdm, store = scope_filters
    retail = inv_stats['retail_codes']
    by_code = inv_stats['by_code']
    if store != 'all':
        codes = {mapping[s]['code'] for s in (valid_stores or []) if s in mapping and mapping[s].get('code')}
    elif rbm != 'all' or bdm != 'all':
        codes = {
            c for c, info in retail.items()
            if (rbm == 'all' or info['rbm'] == rbm) and (bdm == 'all' or info['bdm'] == bdm)
        }
    else:
        codes = set(retail.keys())
    bills = sum(by_code.get(c, {}).get('bills', 0) for c in codes)
    rev = sum(by_code.get(c, {}).get('rev', 0.0) for c in codes)
    return bills, rev


def _per10k(complaints, bills):
    return round(complaints / bills * 10000, 2) if bills else None


def build_advanced_analytics(request):
    """Build full Phase 1 + Phase 2 payload for /api/v1/rca-dashboard/?action=advanced."""
    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    where_store, valid_stores, scope_filters = _resolve_scope(request, mapping)
    dw = resolve_date_window(request, ch)
    fh = _filter_hash(request)
    ck = f"rca_adv_payload_v6:{labels_version()}:{fh}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    if valid_stores == []:
        return {'status': 'empty'}

    st_stats = _get_store_monthly_stats(ch, where_store, dw['date_sql'])
    if not st_stats:
        return {'status': 'empty'}

    min_d, max_d = st_stats['min_d'], st_stats['max_d']
    inv_from = dw['date_from'] or min_d
    inv_to = dw['date_to'] or max_d
    d30 = max_d - timedelta(days=30)
    d60 = max_d - timedelta(days=60)
    d90 = max_d - timedelta(days=90)
    span_days = ((dw['date_to'] - dw['date_from']).days + 1) if (dw['date_from'] and dw['date_to']) else None

    inv_stats = _get_invoice_stats(ch, inv_from, inv_to)
    all_complaints = _get_enriched_complaints(ch, where_store, mapping)
    if dw['date_from'] or dw['date_to']:
        complaints = [r for r in all_complaints if in_date_window(r['date'], dw['date_from'], dw['date_to'])]
    else:
        complaints = all_complaints

    prev_window_rows = None
    if span_days and dw['date_from'] and dw['min_d']:
        p_from = dw['date_from'] - timedelta(days=span_days)
        p_to = dw['date_from'] - timedelta(days=1)
        if p_from >= dw['min_d']:
            prev_window_rows = [r for r in all_complaints if in_date_window(r['date'], p_from, p_to)]

    total_reviews = st_stats['total_all']
    total_complaints = len(complaints)
    positive_reviews = total_reviews - total_complaints
    total_bills, total_rev = _bills_for_scope(inv_stats, scope_filters, mapping, valid_stores)

    # ── 1. Executive KPIs + Priority Quadrant + 12-Month Trend ──────────────────────
    item_agg = defaultdict(lambda: {'n': 0, 'sr': 0, 'neg': 0, 'one': 0, 'l30': 0, 'p30': 0, 'stores': set()})
    month_card = defaultdict(lambda: [0, 0, 0, 0])  # [after_sales, staff, communication, sales]
    card_idx = {c: i for i, c in enumerate(COMPLAINT_CARDS)}
    c_l30 = c_p30 = 0
    avg_sr = 0

    for r in complaints:
        cls = r['cls']
        dt = r['date']
        rt = r['rating']
        avg_sr += rt
        ia = item_agg[cls]
        ia['n'] += 1
        ia['sr'] += rt
        if rt <= 2:
            ia['neg'] += 1
        if rt == 1:
            ia['one'] += 1
        ia['stores'].add(r['store'])
        if dt:
            if dt > d30:
                ia['l30'] += 1
                c_l30 += 1
            elif dt > d60:
                ia['p30'] += 1
                c_p30 += 1
            month_card[dt.replace(day=1)][card_idx[r['card']]] += 1

    prev_item_cnt = Counter(r['cls'] for r in prev_window_rows) if prev_window_rows is not None else None
    short_span = span_days is not None and span_days < 55

    # Quadrant items (all 21 complaint sub-categories)
    quadrant = []
    for c in COMPLAINT_CARDS:
        for k, lbl, _s, _h in CARDS[c]['items']:
            cls = f'{c}/{k}'
            ia = item_agg[cls]
            n = ia['n']
            if short_span:
                l_val = n
                p_val = prev_item_cnt[cls] if prev_item_cnt is not None else 0
                chg_val = _change(l_val, p_val) if (prev_item_cnt is not None and (l_val or p_val)) else None
            else:
                l_val, p_val = ia['l30'], ia['p30']
                chg_val = _change(l_val, p_val) if n else None
            quadrant.append({
                'cls': cls, 'card': c, 'card_title': CARD_TITLES[c], 'key': k,
                'label': lbl, 'short': ITEM_SHORT.get(cls, lbl),
                'count': n,
                'share': _pct(n, total_complaints),
                'avg_rating': round(ia['sr'] / n, 2) if n else 0,
                'neg_rate': _pct(ia['neg'], n),
                'one_star_rate': _pct(ia['one'], n),
                'last_30d': l_val, 'prev_30d': p_val,
                'change_pct': chg_val,
                'stores': len(ia['stores']),
            })
    active_q = [q for q in quadrant if q['count'] > 0]
    vols = sorted(q['count'] for q in active_q)
    sevs = sorted(q['one_star_rate'] for q in active_q)
    quad_medians = {
        'volume': vols[len(vols) // 2] if vols else 0,
        'severity': sevs[len(sevs) // 2] if sevs else 0,
    }

    if dw['period'] != 'all' and span_days and prev_window_rows is not None:
        kpi_l30, kpi_p30 = total_complaints, len(prev_window_rows)
        vel_pct = _change(kpi_l30, kpi_p30)
        vel_title = f"{span_days}d Velocity"
        vel_sub = f"{kpi_l30:,} vs {kpi_p30:,} (prior {span_days}d)"
    elif short_span:
        kpi_l30, kpi_p30 = total_complaints, 0
        vel_pct = None
        vel_title = f"{span_days}d Volume"
        vel_sub = f"{total_complaints:,} complaints (no prior {span_days}d data)"
    else:
        kpi_l30, kpi_p30 = c_l30, c_p30
        vel_pct = _change(c_l30, c_p30)
        vel_title = '30-Day Velocity'
        vel_sub = f"{c_l30:,} (last 30d) vs {c_p30:,} (prev)"

    top_item = max(quadrant, key=lambda x: x['count']) if quadrant else None
    kpis = {
        'total_reviews': total_reviews,
        'positive_reviews': positive_reviews,
        'positive_pct': _pct(positive_reviews, total_reviews),
        'complaints': total_complaints,
        'complaint_pct': _pct(total_complaints, total_reviews, 2),
        'avg_complaint_rating': round(avg_sr / total_complaints, 2) if total_complaints else 0,
        'total_bills': total_bills,
        'total_rev_cr': round(total_rev / 1e7, 1),
        'complaints_per_10k_bills': _per10k(total_complaints, total_bills),
        'last_30d': kpi_l30,
        'prev_30d': kpi_p30,
        'velocity_pct': vel_pct,
        'vel_title': vel_title,
        'vel_sub': vel_sub,
        'top_driver': top_item['label'] if top_item else '—',
        'top_driver_count': top_item['count'] if top_item else 0,
        'period': dw['period'],
        'date_from': str(inv_from),
        'date_to': str(inv_to),
        'as_of': str(dw['max_d'] or max_d),
    }

    # 12-month trend
    months = sorted(set(st_stats['monthly_all'].keys()) | set(month_card.keys()))[-12:]
    trend = {
        'months': [m.strftime('%b %y') for m in months],
        'all_reviews': [st_stats['monthly_all'].get(m, 0) for m in months],
        'series': [
            {
                'card': c, 'label': CARD_TITLES[c],
                'counts': [month_card[m][i] for m in months],
                'rates': [_pct(month_card[m][i], st_stats['monthly_all'].get(m, 0), 2) for m in months],
            }
            for i, c in enumerate(COMPLAINT_CARDS)
        ],
    }

    # ── 2. Store Aggregation (Heatmap + Spike Radar + Store Tab) ────────────────────
    st_comp = defaultdict(lambda: {
        'n': 0, 'sr': 0, 'neg': 0, 'c30': 0, 'p30': 0, 'b60': 0,
        'hb': Counter(), 'items': Counter(),
    })
    net_hb = Counter()
    for r in complaints:
        st = r['store']
        sc = st_comp[st]
        sc['n'] += 1
        sc['sr'] += r['rating']
        if r['rating'] <= 2:
            sc['neg'] += 1
        hb = _CLS_TO_HBUCKET[r['cls']]
        sc['hb'][hb] += 1
        net_hb[hb] += 1
        sc['items'][r['cls']] += 1
        dt = r['date']
        if dt:
            if dt > d30:
                sc['c30'] += 1
            else:
                if dt > d60:
                    sc['p30'] += 1
                if dt > d90:
                    sc['b60'] += 1

    net_hb_rates = {k: _pct(net_hb[k], total_reviews, 3) for k, _, _ in HEATMAP_BUCKETS}

    # Count how many Google profiles share each code so store-level bills stay honest
    code_shares = Counter(
        v.get('code') for s, v in mapping.items()
        if s in st_stats['by_store'] and v.get('code')
    )

    prev_store_cnt = Counter(r['store'] for r in prev_window_rows) if prev_window_rows is not None else None
    store_rows = []
    spikes = []
    for st, den in st_stats['by_store'].items():
        tot_r = den['total']
        sc = st_comp.get(st)
        n = sc['n'] if sc else 0
        info = mapping.get(st, {'rbm': 'Unknown', 'bdm': 'Unknown', 'matched_branch': None, 'code': None})
        code = info.get('code')
        bills = inv_stats['by_code'].get(code, {}).get('bills', 0) if code else 0
        shared_flag = bool(code and code_shares.get(code, 0) > 1)
        top_it = sc['items'].most_common(2) if sc else []
        if short_span:
            c30 = n
            p30 = prev_store_cnt[st] if prev_store_cnt is not None else 0
            st_delta = _change(c30, p30) if (prev_store_cnt is not None and (c30 or p30)) else None
        else:
            c30 = sc['c30'] if sc else 0
            p30 = sc['p30'] if sc else 0
            st_delta = _change(c30, p30) if (c30 or p30) else None
        b60 = sc['b60'] if sc else 0
        r30 = den['r30']
        rb60 = den['r_base60']
        cur_rate = _pct(c30, r30, 2)
        base_rate = _pct(b60, rb60, 2)
        delta_pp = round(cur_rate - base_rate, 2)

        cells = []
        for hk, _hlbl, _ in HEATMAP_BUCKETS:
            cnt = sc['hb'][hk] if sc else 0
            rt_pct = _pct(cnt, tot_r, 2)
            base_net = net_hb_rates[hk]
            excess = round(rt_pct / base_net, 1) if base_net > 0 else 0
            cells.append({'key': hk, 'count': cnt, 'rate': rt_pct, 'excess': excess})

        row = {
            'store': st, 'rbm': info['rbm'], 'bdm': info['bdm'],
            'matched_branch': info.get('matched_branch') or '—',
            'code': code or '', 'shared_code': shared_flag,
            'total_reviews': tot_r, 'complaints': n,
            'complaint_rate': _pct(n, tot_r, 2),
            'bills': bills,
            'per_10k_bills': _per10k(n, bills) if (bills and not shared_flag) else None,
            'avg_rating': round(sc['sr'] / n, 2) if n else 0,
            'last_30d': c30, 'prev_30d': p30,
            'delta_30d_pct': st_delta,
            'top1': ITEM_SHORT.get(top_it[0][0], top_it[0][0]) if len(top_it) >= 1 else '—',
            'top1_cnt': top_it[0][1] if len(top_it) >= 1 else 0,
            'top2': ITEM_SHORT.get(top_it[1][0], top_it[1][0]) if len(top_it) >= 2 else '—',
            'top2_cnt': top_it[1][1] if len(top_it) >= 2 else 0,
            'cells': cells,
        }
        store_rows.append(row)

        if r30 >= 8 and rb60 >= 15:
            spikes.append({
                'store': st, 'rbm': info['rbm'], 'bdm': info['bdm'],
                'r30': r30, 'c30': c30, 'cur_rate': cur_rate,
                'base_rate': base_rate, 'base_c30': round(b60 / 2.0, 1),
                'delta_pp': delta_pp,
                'top1': row['top1'],
            })

    heatmap_stores = sorted(
        store_rows,
        key=lambda s: (-s['complaints'], -s['complaint_rate'], s['store']),
    )
    heatmap = {
        'columns': [{'key': k, 'label': lbl, 'network_rate': net_hb_rates[k]} for k, lbl, _ in HEATMAP_BUCKETS],
        'stores': heatmap_stores,
    }
    spike_radar = {
        'worsening': sorted([x for x in spikes if x['c30'] >= 3 and x['delta_pp'] > 0], key=lambda x: -x['delta_pp'])[:10],
        'improving': sorted([x for x in spikes if x['delta_pp'] < 0], key=lambda x: x['delta_pp'])[:10],
        'rising_items': sorted(
            [q for q in quadrant if q['last_30d'] >= 3 and q['change_pct'] is not None],
            key=lambda q: -q['change_pct'],
        )[:8],
    }

    # ── 3. RBM & BDM Scorecards (deduped branch_master bills) ───────────────────────
    def build_mgr_scorecard(kind):
        # Reviews & complaints per manager from store_rows
        mgr = defaultdict(lambda: {
            'stores': set(), 'codes': set(), 'reviews': 0, 'complaints': 0,
            'sr': 0, 'c30': 0, 'p30': 0, 'items': Counter(),
        })
        for st, den in st_stats['by_store'].items():
            info = mapping.get(st, {'rbm': 'Unknown', 'bdm': 'Unknown'})
            name = info[kind]
            if name == 'Unknown':
                continue
            m = mgr[name]
            m['stores'].add(st)
            m['reviews'] += den['total']
            sc = st_comp.get(st)
            if sc:
                m['complaints'] += sc['n']
                m['sr'] += sc['sr']
                if not short_span:
                    m['c30'] += sc['c30']
                    m['p30'] += sc['p30']
                else:
                    m['c30'] += sc['n']
                m['items'].update(sc['items'])
            if short_span and prev_store_cnt is not None:
                m['p30'] += prev_store_cnt[st]
        # Attach unique retail branch codes for that manager (matching scope if store filter active)
        if scope_filters[2] != 'all':
            for st in (valid_stores or []):
                info = mapping.get(st, {})
                name = info.get(kind, 'Unknown')
                if name != 'Unknown' and info.get('code'):
                    mgr[name]['codes'].add(info['code'])
        else:
            for code, binfo in inv_stats['retail_codes'].items():
                name = binfo[kind]
                if name in mgr:
                    mgr[name]['codes'].add(code)

        out = []
        for name, m in mgr.items():
            bills = sum(inv_stats['by_code'].get(c, {}).get('bills', 0) for c in m['codes'])
            n = m['complaints']
            top_it = m['items'].most_common(2)
            if short_span and prev_store_cnt is None:
                delta_val = None
            else:
                delta_val = _change(m['c30'], m['p30']) if (m['c30'] or m['p30']) else None
            out.append({
                'name': name,
                'stores': len(m['stores']),
                'bills': bills,
                'total_reviews': m['reviews'],
                'complaints': n,
                'complaint_rate': _pct(n, m['reviews'], 2),
                'per_10k_bills': _per10k(n, bills),
                'avg_rating': round(m['sr'] / n, 2) if n else 0,
                'last_30d': m['c30'], 'prev_30d': m['p30'],
                'delta_30d_pct': delta_val,
                'top1': ITEM_SHORT.get(top_it[0][0], top_it[0][0]) if len(top_it) >= 1 else '—',
                'top1_cnt': top_it[0][1] if len(top_it) >= 1 else 0,
                'top2': ITEM_SHORT.get(top_it[1][0], top_it[1][0]) if len(top_it) >= 2 else '—',
                'top2_cnt': top_it[1][1] if len(top_it) >= 2 else 0,
            })
        return sorted(out, key=lambda x: (-x['complaint_rate'], -x['complaints']))

    min_store_rev = 50 if (span_days is None or span_days >= 120) else (15 if span_days >= 45 else 5)
    scorecard = {
        'rbms': build_mgr_scorecard('rbm'),
        'bdms': build_mgr_scorecard('bdm'),
        'min_store_rev': min_store_rev,
        'stores': sorted(
            [s for s in store_rows if s['total_reviews'] >= min_store_rev],
            key=lambda s: (-s['complaint_rate'], -s['complaints']),
        )[:40],
    }

    # ── 4. Product & Brand Friction Matrix ──────────────────────────────────────────
    p_agg = defaultdict(lambda: {'n': 0, 'sr': 0, 'neg': 0, 'cards': Counter(), 'items': Counter()})
    b_agg = defaultdict(lambda: {'n': 0, 'sr': 0, 'neg': 0, 'items': Counter(), 'prods': Counter()})
    for r in complaints:
        for p in r['products']:
            pa = p_agg[p]
            pa['n'] += 1; pa['sr'] += r['rating']
            if r['rating'] <= 2: pa['neg'] += 1
            pa['cards'][r['card']] += 1
            pa['items'][r['cls']] += 1
        for b in r['brands']:
            ba = b_agg[b]
            ba['n'] += 1; ba['sr'] += r['rating']
            if r['rating'] <= 2: ba['neg'] += 1
            ba['items'][r['cls']] += 1
            for p in r['products']:
                ba['prods'][p] += 1

    products_list = []
    for p, pa in sorted(p_agg.items(), key=lambda kv: -kv[1]['n']):
        n = pa['n']
        top_it = pa['items'].most_common(1)[0]
        products_list.append({
            'product': p, 'total': n,
            'share_of_complaints': _pct(n, total_complaints),
            'avg_rating': round(pa['sr'] / n, 2),
            'neg_rate': _pct(pa['neg'], n),
            'after_sales': pa['cards']['after_sales'],
            'staff': pa['cards']['staff'],
            'communication': pa['cards']['communication'],
            'sales': pa['cards']['sales'],
            'top_issue': ITEM_SHORT.get(top_it[0], top_it[0]),
            'top_issue_cls': top_it[0],
            'top_issue_cnt': top_it[1],
        })

    brands_list = []
    for b, ba in sorted(b_agg.items(), key=lambda kv: -kv[1]['n'])[:20]:
        n = ba['n']
        top_its = ba['items'].most_common(2)
        top_p = ba['prods'].most_common(1)
        brands_list.append({
            'brand': b, 'total': n,
            'avg_rating': round(ba['sr'] / n, 2),
            'neg_rate': _pct(ba['neg'], n),
            'top_product': top_p[0][0] if top_p else '—',
            'top1': ITEM_SHORT.get(top_its[0][0], top_its[0][0]) if top_its else '—',
            'top1_cnt': top_its[0][1] if top_its else 0,
            'top2': ITEM_SHORT.get(top_its[1][0], top_its[1][0]) if len(top_its) > 1 else '—',
            'top2_cnt': top_its[1][1] if len(top_its) > 1 else 0,
        })

    # ── 5. Compliance & Mis-Selling Watchlist ───────────────────────────────────────
    r_agg = {k: {'n': 0, 'l30': 0, 'sr': 0, 'stores': Counter(), 'cands': []} for k in RISK_META}
    for r in complaints:
        for rk in r['risks']:
            ra = r_agg[rk]
            ra['n'] += 1
            ra['sr'] += r['rating']
            if r['date'] and r['date'] > d30:
                ra['l30'] += 1
            ra['stores'][r['store']] += 1
            ra['cands'].append(r)

    # Pick distinct, single-topic-first lead samples across the 6 cards so two cards
    # never show the same multi-flagged quote.
    used_lead_sigs = set()
    watchlist = []
    for rk, lbl, sev, desc, pat in RISK_DEFS:
        ra = r_agg[rk]
        n = ra['n']
        ranked = sorted(
            ra['cands'],
            key=lambda x: (
                1 if re.sub(r'\s+', ' ', x['comment']).strip().lower()[:120] in used_lead_sigs else 0,
                0 if len(x['risks']) == 1 else 1,
                0 if (pat is not None and pat.search(x['comment'][:160])) else 1,
                x['rating'],
                -(x['date'].toordinal() if x['date'] else 0),
            ),
        )
        samples = []
        seen_sigs = set()
        for x in ranked:
            sig = re.sub(r'\s+', ' ', x['comment']).strip().lower()[:120]
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            if not samples:
                used_lead_sigs.add(sig)
            samples.append({
                'store': x['store'], 'rbm': x['rbm'], 'rating': x['rating'],
                'date': x['date_str'], 'label': x['label'],
                'comment': x['comment'][:260],
            })
            if len(samples) >= 4:
                break
        watchlist.append({
            'key': rk, 'label': lbl, 'severity': sev, 'description': desc,
            'count': n, 'last_30d': ra['l30'],
            'avg_rating': round(ra['sr'] / n, 2) if n else 0,
            'top_stores': [{'store': s, 'count': c} for s, c in ra['stores'].most_common(5)],
            'samples': samples,
        })

    result = {
        'status': 'ok',
        'kpis': kpis,
        'quadrant': quadrant,
        'quad_medians': quad_medians,
        'trend': trend,
        'heatmap': heatmap,
        'spike_radar': spike_radar,
        'scorecard': scorecard,
        'products': products_list,
        'brands': brands_list,
        'watchlist': watchlist,
    }
    cache.set(ck, result, 300)
    return result


def _filter_complaints(request, complaints):
    card = request.GET.get('card', 'all').strip()
    item = request.GET.get('item', 'all').strip()
    rating = request.GET.get('rating', 'all').strip()
    risk = request.GET.get('risk', 'all').strip()
    product = request.GET.get('product', 'all').strip()
    brand = request.GET.get('brand', 'all').strip()
    q = request.GET.get('q', '').strip().lower()

    out = []
    for r in complaints:
        if card != 'all' and r['card'] != card:
            continue
        if item != 'all' and r['cls'] != item and r['item'] != item:
            continue
        if rating != 'all' and str(r['rating']) != rating:
            continue
        if risk != 'all' and risk not in r['risks']:
            continue
        if product != 'all' and product not in r['products']:
            continue
        if brand != 'all' and brand not in r['brands']:
            continue
        if q and (q not in r['comment_l'] and q not in r['store'].lower()
                  and q not in r['rbm'].lower() and q not in r['bdm'].lower()):
            continue
        out.append(r)

    sort = request.GET.get('sort', 'newest').strip()
    if sort == 'lowest':
        out.sort(key=lambda x: (x['rating'], -(x['date'].toordinal() if x['date'] else 0)))
    elif sort == 'longest':
        out.sort(key=lambda x: (-len(x['comment']), x['rating']))
    else:
        out.sort(key=lambda x: (-(x['date'].toordinal() if x['date'] else 0), x['rating']))
    return out


def _scoped_complaints(request, ch, mapping, where_store, valid_stores):
    dw = resolve_date_window(request, ch)
    if valid_stores == []:
        return [], dw
    all_rows = _get_enriched_complaints(ch, where_store, mapping)
    if dw['date_from'] or dw['date_to']:
        rows = [r for r in all_rows if in_date_window(r['date'], dw['date_from'], dw['date_to'])]
    else:
        rows = all_rows
    return _filter_complaints(request, rows), dw


def query_explorer(request):
    """Paginated verbatim complaint search/filter endpoint."""
    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    where_store, valid_stores, _ = _resolve_scope(request, mapping)
    if valid_stores == []:
        return {'status': 'empty', 'total': 0, 'rows': []}
    filtered, _dw = _scoped_complaints(request, ch, mapping, where_store, valid_stores)

    page = max(1, int(request.GET.get('page', 1) or 1))
    page_size = min(100, max(10, int(request.GET.get('page_size', 25) or 25)))
    total = len(filtered)
    start = (page - 1) * page_size
    page_rows = [
        {
            'store': r['store'], 'rbm': r['rbm'], 'bdm': r['bdm'],
            'rating': r['rating'], 'date': r['date_str'],
            'card': r['card'], 'card_title': CARD_TITLES.get(r['card'], r['card']),
            'cls': r['cls'], 'label': r['label'],
            'products': r['products'], 'brands': r['brands'],
            'risks': [{'key': rk, 'label': RISK_META[rk][0], 'severity': RISK_META[rk][1]} for rk in r['risks']],
            'comment': r['comment'],
        }
        for r in filtered[start:start + page_size]
    ]
    return {
        'status': 'ok',
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': max(1, (total + page_size - 1) // page_size),
        'rows': page_rows,
    }


def export_rca_excel(request):
    """Build a multi-sheet executive Excel workbook matching the active dashboard & explorer filters."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    adv = build_advanced_analytics(request)
    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    where_store, valid_stores, scope = _resolve_scope(request, mapping)
    complaints, dw = _scoped_complaints(request, ch, mapping, where_store, valid_stores)

    wb = openpyxl.Workbook()
    hdr_font = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
    hdr_fill = PatternFill('solid', fgColor='0F172A')

    def style_header(ws):
        ws.freeze_panes = 'A2'
        for cell in ws[1]:
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

    def auto_width(ws, cap=48):
        for col in ws.columns:
            w = max(len(str(c.value or '')) for c in col[:200])
            ws.column_dimensions[col[0].column_letter].width = min(max(w + 3, 12), cap)

    # Sheet 1: Sub-categories
    ws1 = wb.active
    ws1.title = 'Root Causes Summary'
    ws1.append(['Pillar', 'Sub-Category', 'Complaints', 'Share of Complaints %', 'Avg Rating', '1-2 Star %', '1 Star %', 'Last 30d', 'Prev 30d', '30d Change %', 'Stores'])
    if adv.get('status') == 'ok':
        for q in sorted(adv['quadrant'], key=lambda x: -x['count']):
            ws1.append([q['card_title'], q['label'], q['count'], q['share'], q['avg_rating'],
                        q['neg_rate'], q['one_star_rate'], q['last_30d'], q['prev_30d'], q['change_pct'], q['stores']])
    style_header(ws1); auto_width(ws1)

    # Sheet 2: RBM & BDM Scorecard
    ws2 = wb.create_sheet('RBM & BDM Scorecard')
    ws2.append(['Level', 'Manager', 'Stores', 'Invoices (Bills)', 'Total Reviews', 'Complaints', 'Complaint Rate %', 'Complaints / 10k Bills', 'Avg Rating', 'Last 30d', 'Prev 30d', '30d Change %', '#1 Driver', '#1 Count', '#2 Driver', '#2 Count'])
    if adv.get('status') == 'ok':
        for lvl, rows in (('RBM', adv['scorecard']['rbms']), ('BDM', adv['scorecard']['bdms'])):
            for m in rows:
                ws2.append([lvl, m['name'], m['stores'], m['bills'], m['total_reviews'], m['complaints'],
                            m['complaint_rate'], m['per_10k_bills'], m['avg_rating'], m['last_30d'], m['prev_30d'],
                            m['delta_30d_pct'], m['top1'], m['top1_cnt'], m['top2'], m['top2_cnt']])
    style_header(ws2); auto_width(ws2)

    # Sheet 3: Store Heatmap & Spikes
    ws3 = wb.create_sheet('Store Diagnostics')
    if adv.get('status') == 'ok':
        cols = [c['label'] for c in adv['heatmap']['columns']]
        ws3.append(['Store', 'RBM', 'BDM', 'Total Reviews', 'Complaints', 'Complaint Rate %', 'Invoices', 'Complaints / 10k Bills', 'Avg Rating', 'Last 30d', 'Prev 30d'] + cols)
        for s in adv['heatmap']['stores']:
            ws3.append([s['store'], s['rbm'], s['bdm'], s['total_reviews'], s['complaints'], s['complaint_rate'],
                        s['bills'], s['per_10k_bills'], s['avg_rating'], s['last_30d'], s['prev_30d']] +
                       [c['count'] for c in s['cells']])
    style_header(ws3); auto_width(ws3)

    # Sheet 4: Product & Brand Friction
    ws4 = wb.create_sheet('Product & Brand Friction')
    ws4.append(['Type', 'Name', 'Complaints', 'Avg Rating', '1-2 Star %', 'After-Sales', 'Staff', 'Communication', 'Sales & Store', 'Top Issue'])
    if adv.get('status') == 'ok':
        for p in adv['products']:
            ws4.append(['Product Category', p['product'], p['total'], p['avg_rating'], p['neg_rate'],
                        p['after_sales'], p['staff'], p['communication'], p['sales'], f"{p['top_issue']} ({p['top_issue_cnt']})"])
        for b in adv['brands']:
            ws4.append(['Brand', b['brand'], b['total'], b['avg_rating'], b['neg_rate'],
                        '', '', '', '', f"{b['top1']} ({b['top1_cnt']}) / {b['top2']} ({b['top2_cnt']})"])
    style_header(ws4); auto_width(ws4)

    # Sheet 5: Filtered Verbatim Reviews
    ws5 = wb.create_sheet('Verbatim Reviews')
    ws5.append(['Date', 'Store', 'RBM', 'BDM', 'Rating', 'Pillar', 'Sub-Category', 'Products', 'Brands', 'Risk Flags', 'Comment'])
    for r in complaints:
        ws5.append([
            r['date_str'], r['store'], r['rbm'], r['bdm'], r['rating'],
            CARD_TITLES.get(r['card'], r['card']), r['label'],
            ', '.join(r['products']), ', '.join(r['brands']),
            ', '.join(RISK_META[k][0] for k in r['risks']),
            r['comment'],
        ])
    style_header(ws5); auto_width(ws5, cap=70)

    buf = io.BytesIO()
    wb.save(buf)
    tag = '_'.join(x for x in scope if x != 'all') or 'All_Stores'
    if dw['period'] != 'all':
        tag += f"_{dw['period']}"
    tag = re.sub(r'[^A-Za-z0-9_-]+', '_', tag)
    return buf.getvalue(), f"myG_Review_RCA_{tag}_{date.today().isoformat()}.xlsx"
