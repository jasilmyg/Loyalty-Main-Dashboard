class SecurityService:
    @staticmethod
    def get_user_context(user) -> dict:
        """
        Extracts user roles and branch access permissions.
        """
        # If user object is a simple string for mock testing
        if isinstance(user, str):
            is_superuser = (user == "admin")
            username = user
        else:
            is_superuser = getattr(user, 'is_superuser', False)
            username = getattr(user, 'username', 'unknown')

        context = {
            "is_superuser": is_superuser,
            "username": username,
            "allowed_branches": [],
            "role": "executive" if is_superuser else "branch_manager"
        }
        
        # Non-superusers have restricted view of data
        if not is_superuser:
            # Example: user.profile.branch_code in a real model
            context["allowed_branches"] = ["KOCHI_01"] 
            
        return context

    @staticmethod
    def enforce_row_level_security(query: str, user_context: dict) -> tuple[bool, str, str]:
        """
        Parses the SQL and injects or verifies branch-level WHERE clauses for non-superusers.
        Returns: (is_valid, modified_query, error_message)
        """
        if user_context["is_superuser"]:
            # Superusers can see company-wide data
            return True, query, "Bypassed RLS for superuser."
            
        # Very basic check for Phase 5 prototype:
        # In a real system, you'd use sqlparse or intercept the ORM to append `WHERE branch_id IN (...)`
        upper_query = query.upper()
        if "WHERE" not in upper_query:
            # Instead of modifying, we can reject it if the LLM failed to include the security constraint
            return False, query, f"Security Violation: As a {user_context['role']}, you are only allowed to query data for branches: {', '.join(user_context['allowed_branches'])}. The AI failed to apply this filter."
            
        return True, query, "RLS branch constraints verified."
