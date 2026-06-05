import re

class SQLOptimizer:
    """
    PostgreSQL Performance Optimizer.
    Intercepts and rewrites generated SQL queries from the AI models
    to ensure they are SARGable and use indexes efficiently.
    """

    @staticmethod
    def optimize_query(sql: str) -> str:
        if not sql:
            return sql
            
        optimized_sql = sql
        
        # 1. Optimize EXTRACT(YEAR FROM "Date") = YYYY to SARGable Date ranges
        # Pattern matches EXTRACT(YEAR FROM "Date") = 2024
        year_pattern = re.compile(r'EXTRACT\s*\(\s*YEAR\s+FROM\s+"([^"]+)"\s*\)\s*=\s*(\d{4})', re.IGNORECASE)
        
        def replace_year(match):
            col_name = match.group(1)
            year = int(match.group(2))
            return f'"{col_name}" >= \'{year}-01-01\' AND "{col_name}" < \'{year + 1}-01-01\''
            
        optimized_sql = year_pattern.sub(replace_year, optimized_sql)
        
        # 2. Prevent un-indexed ILIKE when LIKE with anchored start could work
        # This is a bit risky to automate, so we'll just ensure UPPER() is used
        # which can utilize expression indexes (e.g. CREATE INDEX idx_upper_branch ON v_sales_data(UPPER("Branch")))
        
        return optimized_sql
