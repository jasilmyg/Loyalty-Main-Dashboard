def get_branch_mappings(ch):
    """
    Returns two dictionaries:
    code_to_name: {'ADF': 'ADIMALY FUTURE', ...}
    name_to_code: {'ADIMALY FUTURE': 'ADF', ...}
    """
    rows = ch.query("SELECT code, branch_name FROM branch_master WHERE code != ''").result_rows
    code_to_name = {}
    name_to_code = {}
    for r in rows:
        code = r[0]
        name = r[1] if r[1] else code
        code_to_name[code] = name
        name_to_code[name] = code
    return code_to_name, name_to_code
