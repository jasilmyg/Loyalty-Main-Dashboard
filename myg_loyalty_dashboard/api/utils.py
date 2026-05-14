import re

VALID_MOBILE_REGEX = r'^[0-9]{10}$'
EXCLUDED_MOBILES = ('1313131313', '0000000000', '9999999999')

def parse_date(d_str):
    if not d_str:
        return None
    if re.match(r'^\d{2}-\d{2}-\d{4}$', d_str):
        d, m, y = d_str.split('-')
        return f'{y}-{m}-{d}'
    return d_str

def build_where_clause(filters, params_start_idx=1):
    conditions, params = [], []
    idx = params_start_idx

    branch = filters.get('branch')
    staff = filters.get('staff')
    start_date = parse_date(filters.get('start_date'))
    end_date = parse_date(filters.get('end_date'))

    if branch and branch.lower() not in ('all branches', 'all', ''):
        conditions.append(f'UPPER("Branch") = UPPER(${idx})')
        params.append(branch)
        idx += 1
    
    if staff:
        conditions.append(f'UPPER("Staff") = UPPER(${idx})')
        params.append(staff)
        idx += 1

    if start_date:
        conditions.append(f'month_date >= ${idx}::DATE')
        params.append(start_date)
        idx += 1

    if end_date:
        conditions.append(f'month_date <= ${idx}::DATE')
        params.append(end_date)
        idx += 1

    where_sql = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
    return where_sql, params
