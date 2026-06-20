import os
import time
import requests
import json

# ───────────────────────────────────────────────────────────────────────────
# CONFIRMED WORKING: nvidia/nemotron-3-ultra-550b-a55b:free on OpenRouter
# Tested: 7.4s response time. Only working model on this OpenRouter account.
# NVIDIA NIM (integrate.api.nvidia.com) is NOT reachable from this network.
# ───────────────────────────────────────────────────────────────────────────

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
WORKING_MODEL    = "nvidia/nemotron-3-ultra-550b-a55b:free"   # confirmed 7.4s response
OR_HEADERS = {
    "Authorization": "Bearer " + OPENROUTER_KEY,
    "Content-Type":  "application/json",
    "HTTP-Referer":  "https://myg-loyalty.com",
    "X-Title":       "myG Loyalty AI",
}


class AnalystAgent:
    """Uses nvidia/nemotron-3-ultra-550b-a55b:free via OpenRouter (confirmed working, ~7-40s)."""

    def __init__(self):
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # CORE: Single API call with reasoning enabled + retry + fallback
    # ─────────────────────────────────────────────────────────────────────────
    def _call_api(self, messages: list, timeout: int = 90) -> dict:
        """
        Calls nvidia/nemotron-3-ultra-550b-a55b:free via OpenRouter.
        This is the only confirmed-working model on this account (~7-40s).
        """
        payload = {
            "model":      WORKING_MODEL,
            "messages":   messages,
            "max_tokens": 4096,
        }
        try:
            r = requests.post(
                OPENROUTER_URL,
                headers=OR_HEADERS,
                json=payload,
                timeout=timeout
            )
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            content = msg.get("content", "").strip()
            if content:
                msg["_model_used"] = WORKING_MODEL
                return msg
            raise ValueError("Empty response from Nemotron")
        except Exception as e:
            raise RuntimeError(f"Nemotron unavailable: {e}")





    # ─────────────────────────────────────────────────────────────────────────
    # MAIN: Deep structured analysis with comprehensive report format
    # ─────────────────────────────────────────────────────────────────────────
    def analyze_results(
        self,
        user_prompt:       str,
        sql_query:         str,
        sql_results:       list,
        previous_messages: list = None
    ) -> dict:
        """
        Produces a comprehensive, structured business analysis report.

        Multi-turn protocol:
        - If previous_messages is empty/None -> fresh Turn 1 call
        - If previous_messages has prior assistant reasoning -> Turn 2+ continuation
          (reasoning_details preserved unmodified so Nemotron resumes chain-of-thought)

        Returns dict with text, reasoning_details, and messages_to_save.
        """
        import datetime
        current_date  = datetime.date.today().strftime("%d %B %Y")
        current_month = datetime.date.today().strftime("%B %Y")

        n_results    = len(sql_results)
        results_data = json.dumps(sql_results[:100], default=str, indent=2)

        system_prompt = f"""You are **myG Enterprise Business Intelligence AI** — a senior data analyst and business strategist for myG, a leading Kerala-based consumer electronics retail chain.

ANALYSIS DATE: {current_date}
DATABASE: January 2020 to May 2026 (June 2026 = current month, PARTIAL data only — exclude from trends)

---
COMPANY PROFILE:
- myG Electronics sells TVs, smartphones, home appliances, laptops, and accessories
- 80+ branches across all Kerala districts (Kochi, Thrissur, Kozhikode, Thiruvananthapuram, Kannur, Palakkad, etc.)
- "Future" prefix = premium flagship store format
- AMJ 2026 Quarter Target: 4,00,000 (4 lakh) repeat customers
- Repeat customer = customer who has previously purchased before the current period

COLUMN DICTIONARY:
- Customer Mobile = unique customer identifier (mobile number)
- Total Value = gross sale in Indian Rupees (INR)
- Finance = EMI/loan amount financed by customer
- Cash / Debit Card / Credit Card = payment mode amounts
- UPI Cashback = amount paid via UPI with cashback
- Point Redemption = loyalty points redeemed (INR equivalent)
- Gift Voucher = gift voucher redemption amount
- Customer Type = New (first purchase ever) or Repeat (returning buyer)
- Branch = store name/city
- RBM = Regional Business Manager | BDM = Business Development Manager
- Invoice Number = unique bill/transaction ID

---
USER QUESTION: "{user_prompt}"

SQL EXECUTED:
{sql_query}

LIVE DATA ({n_results} rows, showing up to 100):
{results_data}

---
WRITE A DETAILED BUSINESS ANALYSIS REPORT using EXACTLY this structure:

## 📋 Executive Summary
Answer the user's question directly in 2-3 sentences. State the PRIMARY number(s) in **bold**. Use Indian number format (e.g., **2,34,891** customers = 2.34 lakh; **₹45,67,89,012** = ₹45.67 crore).

## 📊 Key Metrics Breakdown
Present ALL numbers from the data in a clear format. Include a table if multiple rows. Calculate and include:
- The main metric asked (bolded)
- Secondary metrics from the data
- Derived metrics: percentages, ratios, per-customer averages, penetration rates
- Comparison context where known (e.g., EMI penetration in Indian electronics retail ~35-45%)

## 📈 Deep Business Insights
Provide 4-6 bullet points of analysis. Each bullet must:
- Quote the SPECIFIC data point (with exact number)
- Explain WHAT it means for myG's business
- Identify WHY this pattern exists (seasonality, market trend, campaign effect, etc.)
- Compare vs prior period or benchmark IF data allows

## 🎯 Actionable Recommendations
Give 4-5 specific, implementable actions. Format each as:
**[Priority: 🔴 High / 🟡 Medium / 🟢 Low]** — Action title
> Specific details of what to do, who should do it, and expected business impact

## ⚠️ Risks & Alerts
Flag any concerns, anomalies, data gaps, or warning signs in this data.

## 🔄 Suggested Follow-Up Analysis
List 3 specific follow-up questions the manager should ask next to go deeper.

---
MANDATORY RULES:
1. Use ONLY the data provided above. NEVER invent or estimate numbers not in the data.
2. Bold EVERY key metric: **₹12,34,56,789**, **45,231 customers**, **23.4%**
3. Use Indian number formatting: lakhs and crores (NOT millions/billions)
4. Do NOT include or mention the SQL query
5. If any metric is 0 or NULL, explain the business implication
6. Be SPECIFIC to myG's Kerala electronics business — no generic retail advice
7. Provide the MOST DETAILED and INSIGHTFUL analysis possible
"""

        # ── Build the message thread ─────────────────────────────────────────
        if not previous_messages:
            # Turn 1 — fresh conversation
            messages = [{"role": "user", "content": system_prompt}]
        else:
            # Turn 2+ — append to existing thread preserving reasoning_details
            messages = previous_messages.copy()
            last = messages[-1] if messages else {}
            if last.get("role") == "assistant":
                messages.append({"role": "user", "content": system_prompt})
            else:
                messages[-1]["content"] = system_prompt

        # ── API call ─────────────────────────────────────────────────────────
        try:
            turn1_msg         = self._call_api(messages, timeout=150)
            content           = (turn1_msg.get("content") or "").strip()
            reasoning_details = turn1_msg.get("reasoning_details")

            assistant_entry = {
                "role":             "assistant",
                "content":          content,
                "reasoning_details": reasoning_details
            }
            updated_thread = messages + [assistant_entry]

            return {
                "text":              content,
                "role":              "assistant",
                "content":           content,
                "reasoning_details": reasoning_details,
                "messages_to_save":  updated_thread
            }

        except Exception as e:
            # Graceful fallback — always show structured raw data
            if sql_results and not (len(sql_results) == 1 and "error" in sql_results[0]):
                headers_list = list(sql_results[0].keys())
                if len(sql_results) > 1:
                    table_lines = ["| " + " | ".join(h.replace('_', ' ').title() for h in headers_list) + " |"]
                    table_lines.append("|" + "---|" * len(headers_list))
                    for row in sql_results[:30]:
                        cells = [str(row.get(h, "")) for h in headers_list]
                        table_lines.append("| " + " | ".join(cells) + " |")
                    raw_display = "\n".join(table_lines)
                else:
                    raw_display = "\n".join(
                        f"- **{k.replace('_', ' ').title()}:** {v}"
                        for k, v in sql_results[0].items()
                    )
                fallback_text = (
                    f"### \U0001f4ca Query Results\n\n"
                    f"*\u26a0\ufe0f Deep AI analysis temporarily unavailable \u2014 showing raw data below.*\n\n"
                    f"{raw_display}\n\n"
                    f"---\n"
                    f"*\U0001f4a1 Tip: Ask the same question again to get the full Nemotron detailed analysis.*"
                )
            else:
                fallback_text = (
                    "\u26a0\ufe0f **AI analysis temporarily unavailable.** "
                    "Please try again in a few moments. "
                    f"*(Error: {str(e)[:150]})*"
                )

            return {
                "text":              fallback_text,
                "role":              "assistant",
                "content":           fallback_text,
                "reasoning_details": None,
                "messages_to_save":  messages
            }
