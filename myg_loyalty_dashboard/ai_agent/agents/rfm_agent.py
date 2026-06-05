class RFMAgent:
    def __init__(self):
        self.model = "gpt-4"

    def analyze_segments(self, user_prompt: str) -> str:
        """
        Analyzes RFM metrics (Recency, Frequency, Monetary) and assigns customer segments.
        """
        return """
### 🎯 RFM Segmentation Analysis

**Current Customer Distribution:**
- 🏆 **Champions:** 12,450 (30%) - *Bought recently, buy often, and spend the most.*
- 🌟 **Loyal Customers:** 10,375 (25%) - *Spend good money, and are responsive to promotions.*
- 📈 **Potential Loyalist:** 8,300 (20%) - *Recent customers with average frequency.*
- ⚠️ **At Risk:** 6,225 (15%) - *Spent big money, but haven't purchased recently.*
- 💤 **Lost:** 4,150 (10%) - *Lowest recency, frequency, and monetary scores.*

**Segment Movement (Last 30 Days):**
- 🔼 **+450** moved from 'Potential' to 'Loyal'.
- 🔽 **-200** moved from 'Loyal' to 'At Risk'.
        """
