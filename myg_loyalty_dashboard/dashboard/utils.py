def get_branch_mappings(ch):
    """
    Returns two dictionaries:
    code_to_name: {'ADF': 'ADIMALY FUTURE', ...}
    name_to_code: {'ADIMALY FUTURE': 'ADF', ...}
    Returns empty dicts if ch is None (ClickHouse unavailable).
    """
    if ch is None:
        return {}, {}
    try:
        rows = ch.query("SELECT code, branch_name FROM branch_master WHERE code != ''").result_rows
    except Exception as e:
        print(f"[get_branch_mappings] Query failed: {e}")
        return {}, {}
    code_to_name = {}
    name_to_code = {}
    for r in rows:
        code = r[0]
        name = r[1] if r[1] else code
        code_to_name[code] = name
        name_to_code[name] = code
    return code_to_name, name_to_code
