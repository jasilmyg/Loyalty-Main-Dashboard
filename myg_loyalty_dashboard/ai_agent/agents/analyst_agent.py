import requests

class AnalystAgent:
    def __init__(self):
        self.model = "meta/llama-3.1-8b-instruct"
        self.invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
        self.api_key = "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"

    def analyze_results(self, user_prompt: str, sql_query: str, sql_results: list) -> str:
        """
        Takes raw JSON rows from the database and generates a business insight narrative.
        """
        system_prompt = f"""
You are MYG Loyalty Business Intelligence AI.
The user asked: "{user_prompt}"
The system ran the following SQL query to get the answer:
{sql_query}

The raw data returned by the database is:
{sql_results}

Column meanings in the database:
- Date = Transaction date
- Invoice Number = Unique invoice
- Customer Mobile = Unique customer identifier
- RBM = Regional Business Manager
- BDM = Business Development Manager
- Branch = Store name
- Staff = Sales executive
- Customer Type = New or Repeat customer
- Total Value = Gross sale value
- Discount = Discount amount
- Exchange = Exchange amount
- Finance = Financed amount
- Cash = Cash payment
- Debit Card = Debit card payment
- Credit Card = Credit card payment
- UPI Cashback = Cashback amount
- Point Redemption = Loyalty points redeemed
- Gift Voucher = Voucher value

When answering questions:
1. Understand the business intent.
2. Group by Branch, Staff, RBM, or BDM when relevant.
3. Return exact values from the database.
4. Provide business insights after retrieving data.
5. Average Lifetime Value (LTV) is mathematically defined as Total Revenue divided by Unique Customers.
Always explain assumptions.

Your job is to read this raw data and provide a concise, professional business insight answering the user's question according to the rules above.
DO NOT output the SQL query in your response, just reference what it searched for. Give the answer clearly.
Format the answer in nice Markdown (use bullet points or bold text if helpful).
If the data is empty, say "No data found for this request."
"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}],
            "max_tokens": 512,
            "temperature": 0.3,
            "stream": False
        }

        try:
            response = requests.post(self.invoke_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            return data['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"**Data Results:**\n\n```json\n{sql_results}\n```\n\n*(Insight generation failed: {e})*"
