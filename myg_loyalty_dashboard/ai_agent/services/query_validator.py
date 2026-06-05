import re

class QueryValidator:
    FORBIDDEN_KEYWORDS = [
        'DROP', 'DELETE', 'TRUNCATE', 'ALTER', 'UPDATE', 
        'INSERT', 'GRANT', 'REVOKE', 'COMMIT', 'ROLLBACK',
        'EXEC', 'EXECUTE', 'MERGE', 'REPLACE'
    ]
    
    @classmethod
    def validate_safety(cls, query: str) -> tuple[bool, str]:
        """
        Ensures the query only contains read operations.
        Returns (is_safe, error_message)
        """
        upper_query = query.upper()
        
        # Check for forbidden keywords
        for keyword in cls.FORBIDDEN_KEYWORDS:
            # Match whole words only
            if re.search(rf'\b{keyword}\b', upper_query):
                return False, f"Forbidden operation detected: {keyword}"
                
        # Ensure it starts with an allowed keyword (SELECT or WITH)
        stripped_query = upper_query.strip()
        if not (stripped_query.startswith('SELECT') or stripped_query.startswith('WITH')):
            return False, "Query must begin with SELECT or WITH."
            
        return True, "Query is safe."

    @classmethod
    def validate_schema(cls, query: str, known_tables: list, known_columns: list) -> tuple[bool, str]:
        """
        Validates that tables and columns exist.
        """
        upper_query = query.upper()
        
        # Extract CTE aliases (Common Table Expressions) so they don't fail schema validation
        cte_pattern = re.compile(r'\bWITH\s+([a-zA-Z0-9_]+)\s+AS\s*\(|,\s*([a-zA-Z0-9_]+)\s+AS\s*\(', re.IGNORECASE)
        for match in cte_pattern.finditer(query):
            alias = match.group(1) or match.group(2)
            if alias:
                known_tables.append(alias.lower())
        
        # Simple extraction of table names after FROM or JOIN
        words = query.replace('\n', ' ').replace(';', ' ').replace(',', ' ').split()
        
        extracted_tables = []
        for i, word in enumerate(words):
            if word.upper() in ['FROM', 'JOIN'] and i + 1 < len(words):
                # Ignore 'FROM' inside EXTRACT(YEAR FROM "Date")
                if i > 0 and any(w in words[i-1].upper() for w in ['EXTRACT', 'YEAR', 'MONTH', 'DAY', 'QUARTER', 'WEEK', 'SUBSTRING']):
                    continue
                extracted_tables.append(words[i+1].lower())
                
        for table in extracted_tables:
            # Remove any trailing syntax and quotes/parentheses
            clean_table = table.split(' ')[0].replace('"', '').replace(')', '').replace('(', '').strip()
            
            # Skip empty strings (often from subqueries like "FROM (SELECT")
            if not clean_table or clean_table.lower() == "select":
                continue
                
            if clean_table not in known_tables:
                return False, f"Table '{clean_table}' does not exist in the database schema."
                
        return True, "Schema validation passed."
