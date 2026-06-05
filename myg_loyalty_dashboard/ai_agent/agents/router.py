import re
from typing import Tuple, Optional, Dict, Any

class RouterAgent:
    """
    Intelligent Query Router to classify and route user prompts.
    """
    
    @staticmethod
    def fast_path_kpi(prompt: str) -> Optional[str]:
        """
        Intercept simple KPI questions to bypass the LLM entirely.
        Delegates to the high-performance SQLTemplateEngine.
        """
        from .sql_template_engine import SQLTemplateEngine
        return SQLTemplateEngine.match_template(prompt)

    @staticmethod
    def semantic_path(prompt: str) -> Optional[str]:
        """
        Intercept business definitions and predefined semantic questions
        using the Semantic Search Layer (pgvector).
        """
        from ..services.semantic_matcher import SemanticMatcher
        return SemanticMatcher.match_query(prompt)

    # ─────────────────────────────────────────────────────────────────────────
    # AVAILABLE NVIDIA MODELS REGISTRY
    # ─────────────────────────────────────────────────────────────────────────
    MODELS = {
        # 🌙 Moonshot AI — Kimi K2.6 (best for complex business reasoning & long context)
        "kimi":        "moonshotai/kimi-k2.6",

        # 🦙 Meta — Llama 4 Maverick (business insights, explanations, RFM)
        "llama":       "meta/llama-4-maverick-17b-128e-instruct",

        # ⚡ Meta — Llama 3.1 8B (fastest for general tasks, cohort, root cause)
        "llama_fast":  "meta/llama-3.1-8b-instruct",

        # 🔴 Google Gemma 3n (default SQL generation, simple queries)
        "gemma":       "google/gemma-3n-e4b-it",
    }

    @staticmethod
    def determine_model(prompt: str) -> str:
        """
        Multi-Model Routing: Determines which NVIDIA LLM to use based on prompt type.

        Routing Logic:
          • Uses Llama 3.1 8B for all standard queries to ensure < 5s latency.
          • Kimi K2.6 endpoint is currently unstable/timing out.
        """
        return RouterAgent.MODELS["llama_fast"]

    @staticmethod
    def is_simple_numeric_query(prompt: str) -> bool:
        """
        Determines if the AI response should just be a raw number instead of narrative.
        """
        p = prompt.lower()
        if "count" in p or "how many" in p or "total" in p or "revenue" in p:
            if not any(w in p for w in ["explain", "analyze", "why", "compare", "list"]):
                return True
        return False
