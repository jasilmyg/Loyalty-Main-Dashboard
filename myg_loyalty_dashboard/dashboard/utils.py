# ── Global filter: exclude internal/non-retail branch types ─────────────────
# Applied to all branch_master queries so warehouses, godowns, and head offices
# never appear in any report or dashboard section.
RETAIL_BRANCH_FILTER = (
    "store_type NOT IN ('HEAD OFFICE', 'WAREHOUSE', 'GODOWN') "
    "AND rbm != ''"
)

# Convenience: branch codes that are explicitly excluded (belt-and-braces)
INTERNAL_BRANCH_CODES = {'BOM', 'INFG', 'KRO', 'MRO', 'ROKY', 'SWH'}


def get_branch_mappings(ch):
    """
    Returns two dictionaries (retail branches only):
    code_to_name: {'ADF': 'ADIMALY FUTURE', ...}
    name_to_code: {'ADIMALY FUTURE': 'ADF', ...}
    Returns empty dicts if ch is None (ClickHouse unavailable).
    """
    if ch is None:
        return {}, {}
    try:
        rows = ch.query(
            f"SELECT code, branch_name FROM branch_master "
            f"WHERE code != '' AND {RETAIL_BRANCH_FILTER}"
        ).result_rows
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

