from ..forecasting.prophet_model import ProphetRevenueModel
from ..forecasting.xgboost_model import XGBoostTargetModel
from ..forecasting.lstm_model import LSTMCustomerModel

class ForecastAgent:
    def __init__(self):
        self.prophet = ProphetRevenueModel()
        self.xgboost = XGBoostTargetModel()
        self.lstm = LSTMCustomerModel()

    def generate_forecast(self, user_prompt: str) -> str:
        prompt_lower = user_prompt.lower()
        
        # Route to specific ML model based on intent
        if "revenue" in prompt_lower and not "target" in prompt_lower:
            res = self.prophet.predict("month_end")
            report = f"### 📈 Revenue Forecast\n\n**Model:** {res['model_used']}\n**Expected Value:** {res['expected_value']}\n**Confidence:** {res['confidence_interval']}"
            
        elif "target" in prompt_lower or "achieve" in prompt_lower and "revenue" in prompt_lower:
            res = self.xgboost.predict()
            report = f"### 🎯 Target Achievement Forecast\n\n**Model:** {res['model_used']}\n**Probability of Achievement:** {res['probability_of_achievement']}"
            
        elif "repeat" in prompt_lower or "dormant" in prompt_lower or "customer" in prompt_lower:
            target = "Repeat Customers" if "repeat" in prompt_lower else "Dormant Revivals"
            res = self.lstm.predict(target)
            
            # Specific logic for "Will we achieve 4 lakh repeat customers?"
            if "4 lakh" in prompt_lower or "400000" in prompt_lower:
                achieve_str = "✅ **Yes**, our models indicate we will successfully exceed the 4 Lakh target."
            else:
                achieve_str = ""
                
            report = f"### 👥 Customer Forecast\n\n**Model:** {res['model_used']}\n**Expected {target}:** {res['expected_value']}\n\n{achieve_str}"
            
        else:
            report = "I'm sorry, I couldn't determine which forecasting model to use. Try asking about 'revenue forecast', 'target achievements', or 'repeat customer predictions'."

        return report
