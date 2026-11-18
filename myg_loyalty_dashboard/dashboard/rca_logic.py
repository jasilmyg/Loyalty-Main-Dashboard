import hashlib
import pandas as pd
import difflib
from django.core.cache import cache
from analytics.clickhouse_service import get_ch_client
from dashboard.utils import RETAIL_BRANCH_FILTER
from dashboard.rca_rules import CLS_SQL, CARD_ITEMS, REVIEWS_SRC
from dashboard.rca_labels import get_low_rows, labels_version

STORE_ALIASES = {
    'MYG FUTURE': None,
    'MYG': None,
    'MYG CARE': None,
    'MYG OFFERS': None,
    'UNKNOWN': None,
    'MYG CAFE YMCA': None,
    'MYG SEEMA': None,
    'MYG CHAVAKKAD': 'CHAVAKKAD FUTURE',
    'MYG EDAPPALLY': 'EDAPPALLY FUTURE',
    'MYG FUTURE MANGALURU': 'FALNIR FUTURE',
    'MYG KANNUR': 'KANNUR FUTURE',
    'MYG KATAPPANA': 'KATTAPPANA FUTURE',
    'MYG PATTAMBI': 'PATTAMBI FUTURE',
    'MYG PENTA': 'MENAKA',
    'MYG SABA CENTRE': 'SABA MAHARANI',
    'MYG THAMARASSERY': 'THAMARASSERY FUTURE',
    'MYG CHEMMAD': 'CHEMMAD FUTURE',
    'MYG KOTHAMANGALAM': 'KOTHAMANGALAM FUTURE',
    'MYG KANJIRAPPALLY': 'KANJIRAPPALLY FUTURE',
    'MYG NILAMBUR': 'NILAMBUR FUTURE',
    'MYG PERUMBAVOOR': 'PERUMBAVOOR FUTURE',
    'MYG FUTURE MARINE DRIVE - THE EPIC SHOWROOM': 'MARINE DRIVE FUTURE',
    'MYG FUTURE PANAVILA, TRIVANDRUM': 'PANAVILA FUTURE',
    'MYG FUTURE VYPIN KOCHI': 'VYPIN FUTURE',
    'MYG FUTURE WADAKANCHERY OTTUPARA': 'OTTUPARA FUTURE',
    'MYG BYPASS PKD': 'BYPASS ROAD',
    'MYG THANA KANNUR': 'THANA',
    'MYG TB ROAD PALAKKAD': 'TB ROAD',
    'MYG HILITE': 'HI LITE MALL',
    'MYG MALAPURAM': 'MALAPPURAM',
    'MYG RAMANATUKARA': 'RAMANATTUKARA',
    'MYG KARUNAGAPALLY': 'KARUNAGAPPALLY',
    'MYG CHALAKKUDY': 'CHALAKKUDI',
    'MYG OTTAPPALAM': 'OTTAPALAM',
    'MYG BALUSSERRY': 'BALUSSERY',
    'MYG FUTURE MANNARKKAD': 'MANNARKAD FUTURE',
    'MYG FUTURE VADAKKENCHERY': 'VADAKKENCHERRY FUTURE',
    'MYG FUTURE, MUVATTUPUZHA': 'MUVATTUPUZHA FUTURE',
    'MYG FUTURE KUTTIADY': 'KUTTIYADI FUTURE',
}

# City-wide Google profiles (mapped to region/manager, not a single bill branch code)
CITY_HUB_OVERRIDES = {
    'MYG FUTURE CALICUT': {'rbm': 'ALFAS', 'bdm': 'Unknown', 'matched_branch': None, 'code': None},
    'MYG FUTURE THIRUVANANTHAPURAM': {'rbm': 'VAISAKH', 'bdm': 'AMAL', 'matched_branch': None, 'code': None},
    'MYG FUTURE THRISSUR': {'rbm': 'RENJITH', 'bdm': 'PRASEED', 'matched_branch': None, 'code': None},
}


def get_store_mapping(ch):
    """Returns a dict mapping google_reviews store_name to {rbm, bdm, matched_branch, code}."""
    cached = cache.get("rca_store_mapping_v2")
    if cached:
        return cached
    rows = ch.query(f"SELECT branch_name, rbm, bdm, code FROM branch_master WHERE {RETAIL_BRANCH_FILTER}").result_rows
    ch_dict = {r[0].strip().upper(): {'rbm': r[1], 'bdm': r[2], 'code': (r[3] or '').strip().upper() or None}
               for r in rows if r[0]}
    ch_names = list(ch_dict.keys())

    def normalize_name(name):
        name = str(name).upper().strip()
        name = name.replace('MYG', '').strip()
        if 'FUTURE' in name and not name.endswith('FUTURE'):
            name = name.replace('FUTURE', '').strip() + ' FUTURE'
        if 'THIRUVANANTHAPURAM' in name:
            name = name.replace('THIRUVANANTHAPURAM', 'TRIVANDRUM')
        return name

    google_stores = [r[0] for r in ch.query("SELECT distinct store_name FROM google_reviews").result_rows]

    mapping = {}
    for store in google_stores:
        key = str(store).upper().strip()
        if key in CITY_HUB_OVERRIDES:
            mapping[store] = dict(CITY_HUB_OVERRIDES[key])
            continue
        if key in STORE_ALIASES:
            matched_name = STORE_ALIASES[key]
        else:
            norm = normalize_name(store)
            if norm in ch_dict:
                matched_name = norm
            else:
                matches = difflib.get_close_matches(norm, ch_names, n=1, cutoff=0.8)
                matched_name = matches[0] if matches else None
        info = ch_dict.get(matched_name) if matched_name else None
        mapping[store] = {
            'rbm': info['rbm'] if info else 'Unknown',
            'bdm': info['bdm'] if info else 'Unknown',
            'matched_branch': matched_name,
            'code': info['code'] if info else None,
        }

    if mapping:
        cache.set("rca_store_mapping_v2", mapping, 300)
    return mapping

def build_rca_data(request):
    rbm_filter = request.GET.get('rbm', 'all')
    bdm_filter = request.GET.get('bdm', 'all')
    store_filter = request.GET.get('store', 'all')

    fh = hashlib.md5(f"{rbm_filter}|{bdm_filter}|{store_filter}".encode('utf-8')).hexdigest()[:12]
    cache_key = f"rca_legacy_cards_v4:{labels_version()}:{fh}"
    cached = cache.get(cache_key)
    if cached and cached.get('status') == 'ok' and cached.get('total_reviews', 0) > 0:
        return cached

    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    
    # Find matching store_names for the query
    valid_stores = []
    for store, info in mapping.items():
        if rbm_filter != 'all' and info['rbm'] != rbm_filter:
            continue
        if bdm_filter != 'all' and info['bdm'] != bdm_filter:
            continue
        if store_filter != 'all' and store != store_filter:
            continue
        valid_stores.append(store)
        
    if not valid_stores:
        return {'status': 'empty'}
        
    # We use ALL star reviews as requested by the user, but we only want to categorize
    # the ones that actually have comments. Empty comments are marked 'Other / Unclassified', 'Unclassified'.
    if rbm_filter == 'all' and bdm_filter == 'all' and store_filter == 'all':
        where_store = ""
    else:
        stores_sql = ", ".join([f"'{s.replace(chr(39), chr(39)+chr(39))}'" for s in valid_stores])
        where_store = f" AND store_name IN ({stores_sql})"

    # 4-5 star reviews (no complaints in there): classified by the SQL rules (dashboard.rca_rules).
    res = ch.query(f"""
        SELECT {CLS_SQL} AS cls, count() AS cnt
        FROM {REVIEWS_SRC}
        WHERE rating >= 4{where_store}
        GROUP BY cls
    """).result_rows
    counts = {cls: cnt for cls, cnt in res}
    # Reviews rated 1-3 stars: every one of them was READ and labelled by meaning
    # (dashboard.rca_labels); reviews not yet reviewed fall back to the SQL rules.
    for r in get_low_rows(ch, where_store):
        counts[r['cls']] = counts.get(r['cls'], 0) + 1
    total_reviews = sum(counts.values())

    if total_reviews == 0:
        return {'status': 'empty'}

    data = {'status': 'ok', 'total_reviews': total_reviews}
    for card, items in CARD_ITEMS.items():
        # key order matters: the page JS maps rows by position
        block = {'total': sum(counts.get(f"{card}/{i}", 0) for i in items)}
        for i in items:
            block[i] = counts.get(f"{card}/{i}", 0)
        data[card] = block
    cache.set(cache_key, data, 300)
    return data

def get_rca_filters():
    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    
    hierarchy = []
    rbms = set()
    bdms = set()
    stores = set()
    
    for store, info in mapping.items():
        rbms.add(info['rbm'])
        bdms.add(info['bdm'])
        stores.add(store)
        hierarchy.append({
            'rbm': info['rbm'],
            'bdm': info['bdm'],
            'store': store
        })
        
    return {
        'rbms': sorted(list(r for r in rbms if r != 'Unknown')),
        'bdms': sorted(list(b for b in bdms if b != 'Unknown')),
        'stores': sorted(list(stores)),
        'hierarchy': hierarchy
    }
