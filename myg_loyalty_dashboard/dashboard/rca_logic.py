import pandas as pd
import difflib
from analytics.clickhouse_service import get_ch_client
from dashboard.utils import RETAIL_BRANCH_FILTER

def build_rca_data(request):
    df = pd.read_excel('Store Review.xlsx')
    
    ch = get_ch_client()
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

    mapping = {}
    for store in df['Store Name'].unique():
        norm = normalize_name(store)
        matches = difflib.get_close_matches(norm, ch_names, n=1, cutoff=0.5)
        mapping[store] = matches[0] if matches else None

    df['Matched'] = df['Store Name'].map(mapping)
    df['RBM'] = df['Matched'].apply(lambda x: ch_dict[x]['rbm'] if pd.notna(x) and x else 'Unknown')
    df['BDM'] = df['Matched'].apply(lambda x: ch_dict[x]['bdm'] if pd.notna(x) and x else 'Unknown')
    
    # Apply filters
    rbm_filter = request.GET.get('rbm', 'all')
    bdm_filter = request.GET.get('bdm', 'all')
    store_filter = request.GET.get('store', 'all')
    
    if rbm_filter != 'all':
        df = df[df['RBM'] == rbm_filter]
    if bdm_filter != 'all':
        df = df[df['BDM'] == bdm_filter]
    if store_filter != 'all':
        df = df[df['Store Name'] == store_filter]
        
    # Check for the column name (handle both potential names)
    total_col = 'Total 1 & 2 Reviews' if 'Total 1 & 2 Reviews' in df.columns else 'Total 1★ & 2★ Reviews'
    try:
        total_reviews = int(df[total_col].sum())
    except KeyError:
        # Fallback if both fail, try finding a column that starts with 'Total'
        total_col = [c for c in df.columns if 'Total' in c][0]
        total_reviews = int(df[total_col].sum())
    
    if total_reviews == 0:
        return {'status': 'empty'}
        
    def sum_metric(col):
        # We need to do exact match or partial match for the column name because the new file might not have them
        exact_cols = [c for c in df.columns if col == c or col.replace(' (Count)', '') in c]
        if exact_cols:
            return int(df[exact_cols[0]].sum())
        return 0

    return {
        'status': 'ok',
        'total_reviews': total_reviews,
        'after_sales': {
            'total': sum_metric('After-Sales & Service Friction (Count)'),
            'delays': sum_metric('Severe Delays & Turnaround Lag (Count)'),
            'warranty': sum_metric('Warranty & Replacement Disputes (Count)'),
            'unresolved': sum_metric('Unresolved Faults / Defective Repair (Count)'),
            'updates': sum_metric('Missing Updates & Proactive Follow-up (Count)'),
            'cost': sum_metric('Service Charges & Spare Cost Disputes (Count)'),
            'general': sum_metric('General After-Sales Grievances (Count)'),
        },
        'staff': {
            'total': sum_metric('Staff Interaction & Attitude (Count)'),
            'unprofessional': sum_metric('Unprofessionalism & Knowledge Gaps (Count)'),
            'rude': sum_metric('Rude & Arrogant Behavior / Disrespect (Count)'),
            'neglect': sum_metric('Inattention & Walk-in Neglect (Count)'),
            'bias': sum_metric('Poor Attitude & Budget Bias (Count)'),
            'general': sum_metric('General Staff Dissatisfaction (Count)'),
        },
        'communication': {
            'total': sum_metric('Communication Breakdown (Count)'),
            'chasing': sum_metric('Customer Follow-up Fatigue (Chasing) (Count)'),
            'promises': sum_metric('Broken Promises & No Callbacks (Count)'),
            'rude_phone': sum_metric('Rude Telephonic Response & Tone (Count)'),
            'unanswered': sum_metric('Unanswered Calls, Busy & Disconnected (Count)'),
            'zero_contact': sum_metric('General Zero-Response (Phone/WhatsApp) (Count)'),
        },
        'other': {
            'total': sum_metric('Other / Unclassified (Count)'),
            'unclassified': sum_metric('Other / Unclassified (Count)')
        }
    }

def get_rca_filters():
    df = pd.read_excel('Store Review.xlsx')
    ch = get_ch_client()
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

    mapping = {}
    for store in df['Store Name'].unique():
        norm = normalize_name(store)
        matches = difflib.get_close_matches(norm, ch_names, n=1, cutoff=0.5)
        mapping[store] = matches[0] if matches else None

    df['Matched'] = df['Store Name'].map(mapping)
    df['RBM'] = df['Matched'].apply(lambda x: ch_dict[x]['rbm'] if pd.notna(x) and x else 'Unknown')
    df['BDM'] = df['Matched'].apply(lambda x: ch_dict[x]['bdm'] if pd.notna(x) and x else 'Unknown')
    
    return {
        'rbms': sorted([x for x in df['RBM'].unique() if pd.notna(x) and x != 'Unknown']),
        'bdms': sorted([x for x in df['BDM'].unique() if pd.notna(x) and x != 'Unknown']),
        'stores': sorted([x for x in df['Store Name'].unique() if pd.notna(x)])
    }
