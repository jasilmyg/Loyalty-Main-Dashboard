"""
CORRECT removal of PostgreSQL fallback blocks from services.py.

Strategy: For each except block that has a PG fallback:
  - Replace the PG fallback code INSIDE the except block with a `raise`
  - This lets the exception propagate, and the shared code after try/except 
    would get `NameError` because variables are not set.
  
BETTER Strategy: 
  - For Type A functions (return INSIDE try): except returns empty data  
  - For Type B functions (shared code outside try): replace except with raise

EVEN BETTER: Just replace the PG query lines inside each except block.
Replace `_q(` and `_q1(` calls in except blocks with neutral equivalents.

SIMPLEST correct approach:
  Each fallback block has: rows = _q(...) or row = _q1(...)
  Replace with: rows = [] or row = None  (empty ClickHouse-equivalent)
  This way the shared code after try/except still runs, just with empty data.
"""
import re
from pathlib import Path

src = Path(r"C:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\analytics\services.py")
text = src.read_text(encoding='utf-8')

original_len = len(text)

# ─── Replacement 1: Replace each "print fallback to PG" line + PG query block
# Pattern: find "print(f"[CH] X fallback to PG: {e}")" lines
# These are always the first line in the except block
# Replace the entire except body (from that print to the next dedented code)
# with a simple: print(...) + set empty variables

fallback_patterns = [
    # (old_print_text, function_name, replacement_body)
    
    # sales_overview - Type A (return INSIDE try, so except just returns)
    # Already works: kept original except that falls to PG
    # We just neutralize the PG queries
    
    # customer_analytics - Type B
    (
        r'            print\(f"\[CH\] customer_analytics fallback to PG: \{e\}"\)\n'
        r'            where_sql, params = self._build_where_clause\(filters\)\n'
        r'            row = _q1\(f""".*?""", params\)\n'
        r'            if not row: row = \(0, 0, 0\)\n'
        r'            total_ltv        = float\(row\[0\] or 0\)\n'
        r'            total_customers  = int\(row\[1\] or 0\)\n'
        r'            repeat_customers = int\(row\[2\] or 0\)',
        
        '            print(f"[CH] customer_analytics ClickHouse error: {e}")\n'
        '            total_ltv, total_customers, repeat_customers = 0, 0, 0',
        re.DOTALL
    ),
    
    # frequency_distribution - Type B
    (
        r'            print\(f"\[CH\] frequency_distribution fallback to PG: \{e\}"\)\n'
        r'            where_sql, params = self._build_where_clause\(filters\)\n'
        r'            rows = _q\(f""".*?""", params\)',
        
        '            print(f"[CH] frequency_distribution ClickHouse error: {e}")\n'
        '            rows = []',
        re.DOTALL
    ),
    
    # rfm_segments - Type B
    (
        r'            print\(f"\[CH\] rfm_segments fallback to PG: \{e\}"\)\n'
        r'            rows = _q\(f""".*?""",\s*params\)',
        
        '            print(f"[CH] rfm_segments ClickHouse error: {e}")\n'
        '            rows = []',
        re.DOTALL
    ),
    
    # monetary_quintiles - Type B  
    (
        r'            print\(f"\[CH\] monetary_quintiles fallback to PG: \{e\}"\)\n'
        r'            where_sql, params = self._build_where_clause\(filters\)\n'
        r'            rows = _q\(f""".*?""", params\)',
        
        '            print(f"[CH] monetary_quintiles ClickHouse error: {e}")\n'
        '            rows = []',
        re.DOTALL
    ),
    
    # cohort_retention - complex, Type B
    (
        r'            print\(f"\[CH\] cohort_retention fallback to PG: \{e\}"\)\n'
        r'        rows = _q\(f""".*?""",\s*\[\]\)',
        
        '            print(f"[CH] cohort_retention ClickHouse error: {e}")\n'
        '        rows = []',
        re.DOTALL
    ),
    
    # segmentation_matrix - Type B
    (
        r'            print\(f"\[CH\] segmentation_matrix fallback to PG: \{e\}"\)\n'
        r'            where_sql, params = self._build_where_clause\(filters\)\n'
        r'            rows = _q\(f""".*?""", params\)',
        
        '            print(f"[CH] segmentation_matrix ClickHouse error: {e}")\n'
        '            rows = []',
        re.DOTALL
    ),
    
    # retail_loyalty_matrix - complex
    (
        r'            print\(f"\[CH\] retail_loyalty_matrix fallback to PG: \{e\}"\).*?(?=\n        # ─)',
        
        '            print(f"[CH] retail_loyalty_matrix ClickHouse error: {e}")\n'
        '            return {}',
        re.DOTALL
    ),
    
    # fy_loyalty_report
    (
        r'            print\(f"\[CH\] fy_loyalty_report fallback to PG: \{e\}"\).*?(?=\n    # ──)',
        
        '            print(f"[CH] fy_loyalty_report ClickHouse error: {e}")\n'
        '            rows = []',
        re.DOTALL
    ),
    
    # fy_sales_report
    (
        r'            print\(f"\[CH\] fy_sales_report fallback to PG: \{e\}"\).*?(?=\n    # ──|\Z)',
        
        '            print(f"[CH] fy_sales_report ClickHouse error: {e}")\n'
        '            rows = []',
        re.DOTALL
    ),
]

count = 0
for pattern, replacement, flags in fallback_patterns:
    new_text, n = re.subn(pattern, replacement, text, flags=flags)
    if n:
        print(f"  ✓ Replaced: {pattern[:70]}...")
        text = new_text
        count += n
    else:
        print(f"  ✗ NOT FOUND: {pattern[:70]}...")

src.write_text(text, encoding='utf-8')
print(f"\nDone. {count} PG fallback blocks neutralized.")
print(f"File size: {len(text):,} bytes (was {original_len:,} bytes)")
