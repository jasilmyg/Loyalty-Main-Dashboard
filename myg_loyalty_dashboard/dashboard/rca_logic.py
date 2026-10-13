import pandas as pd
import difflib
from analytics.clickhouse_service import get_ch_client
from dashboard.utils import RETAIL_BRANCH_FILTER
from dashboard.rca_rules import CLS_SQL, CARD_ITEMS

def get_store_mapping(ch):
    """Returns a dict mapping google_reviews store_name to {rbm, bdm, matched_branch}"""
    rows = ch.query(f"SELECT branch_name, rbm, bdm FROM branch_master WHERE {RETAIL_BRANCH_FILTER}").result_rows
    ch_dict = {r[0].strip().upper(): {'rbm': r[1], 'bdm': r[2]} for r in rows if r[0]}
    ch_names = list(ch_dict.keys())
    
    def normalize_name(name):
        name = str(name).upper().strip()
        name = name.replace('MYG', '').strip()
        if 'FUTURE' in name and not name.endswith('FUTURE'):
            name = name.replace('FUTURE', '').strip() + ' FUTURE'
        if 'THIRUVANANTHAPURAM' in name:
            name = name.replace('THIRUVANANTHAPURAM', 'TRIVANDRUM')
        return name

    google_stores_res = ch.query("SELECT distinct store_name FROM google_reviews").result_rows
    google_stores = [r[0] for r in google_stores_res]
    
    mapping = {}
    for store in google_stores:
        norm = normalize_name(store)
        matches = difflib.get_close_matches(norm, ch_names, n=1, cutoff=0.5)
        matched_name = matches[0] if matches else None
        rbm = ch_dict[matched_name]['rbm'] if matched_name else 'Unknown'
        bdm = ch_dict[matched_name]['bdm'] if matched_name else 'Unknown'
        mapping[store] = {'rbm': rbm, 'bdm': bdm, 'matched_branch': matched_name}
        
    return mapping

def build_rca_data(request):
    ch = get_ch_client()
    mapping = get_store_mapping(ch)
    
    rbm_filter = request.GET.get('rbm', 'all')
    bdm_filter = request.GET.get('bdm', 'all')
    store_filter = request.GET.get('store', 'all')
    
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
    stores_sql = ", ".join([f"'{s.replace(chr(39), chr(39)+chr(39))}'" for s in valid_stores])
    
    # Query for total reviews (all star ratings)
    total_reviews = ch.query(f"SELECT count() FROM google_reviews WHERE store_name IN ({stores_sql})").result_rows[0][0]
    
    if total_reviews == 0:
        return {'status': 'empty'}
        
    # Rating-aware classification (see dashboard.rca_rules): 1-2★ (or clearly negative 3★) reviews
    # are issues and are sorted into the three issue cards; everything else is Positive & General.
    res = ch.query(f"""
        SELECT {CLS_SQL} AS cls, count() AS cnt
        FROM google_reviews
        WHERE store_name IN ({stores_sql})
        GROUP BY cls
    """).result_rows
    counts = {cls: cnt for cls, cnt in res}

    data = {'status': 'ok', 'total_reviews': total_reviews}
    for card, items in CARD_ITEMS.items():
        # key order matters: the page JS maps rows by position
        block = {'total': sum(counts.get(f"{card}/{i}", 0) for i in items)}
        for i in items:
            block[i] = counts.get(f"{card}/{i}", 0)
        data[card] = block
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
