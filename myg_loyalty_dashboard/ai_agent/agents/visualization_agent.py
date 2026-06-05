import uuid

class VisualizationAgent:
    def __init__(self):
        self.model = "gpt-4"

    def generate_dynamic_chart(self, user_prompt: str, sql_agent, user_context) -> dict:
        """
        Dynamically generates a real chart by letting SQLAgent fetch the data first,
        then converting the results into Plotly JSON.
        """
        import time
        import uuid
        
        # 1. Let SQLAgent generate the query
        generated_sql, error_msg = sql_agent.generate_query(user_prompt, user_context, model_name="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        if error_msg:
            return {"error": error_msg}
            
        # 2. Execute SQL
        results = sql_agent.execute_query(generated_sql)
        if not results or "error" in results[0]:
            return {"error": "Failed to retrieve chart data from database."}
            
        # 3. Analyze columns to determine chart type
        headers = list(results[0].keys())
        if len(headers) < 2:
            return {"error": "Insufficient columns for visualization."}
            
        x_col = headers[0]
        y_col = headers[1]
        
        x_data = [str(row[x_col]) for row in results]
        y_data = [row[y_col] for row in results]
        
        prompt_lower = user_prompt.lower()
        chart_id = f"chart_{uuid.uuid4().hex[:8]}"
        
        if "trend" in prompt_lower or "date" in x_col.lower() or "month" in x_col.lower():
            # Sort by x_data to prevent zig-zag lines if SQL didn't ORDER BY
            try:
                sorted_pairs = sorted(zip(x_data, y_data), key=lambda pair: pair[0])
                x_data = [pair[0] for pair in sorted_pairs]
                y_data = [pair[1] for pair in sorted_pairs]
            except Exception:
                pass

            # Line Chart
            return {
                "id": chart_id,
                "data": [{
                    "x": x_data,
                    "y": y_data,
                    "type": "scatter",
                    "mode": "lines+markers",
                    "marker": {"color": "#10b981"},
                    "line": {"shape": "spline", "width": 3}
                }],
                "layout": {
                    "title": "Dynamic Trend Analysis",
                    "xaxis": {"title": str(x_col).capitalize()},
                    "yaxis": {"title": str(y_col).capitalize()},
                    "template": "plotly_white",
                    "margin": {"l": 50, "r": 20, "t": 50, "b": 50}
                }
            }
        elif "distribution" in prompt_lower or "share" in prompt_lower or "percentage" in prompt_lower:
            # Pie Chart
            return {
                "id": chart_id,
                "data": [{
                    "values": y_data,
                    "labels": x_data,
                    "type": "pie",
                    "hole": 0.4,
                    "marker": {
                        "colors": ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#64748b"]
                    }
                }],
                "layout": {
                    "title": "Dynamic Distribution",
                    "template": "plotly_white",
                    "margin": {"l": 20, "r": 20, "t": 50, "b": 20}
                }
            }
        else:
            # Bar Chart
            return {
                "id": chart_id,
                "data": [{
                    "x": x_data,
                    "y": y_data,
                    "type": "bar",
                    "marker": {"color": "#3b82f6"}
                }],
                "layout": {
                    "title": "Dynamic Comparison",
                    "xaxis": {"title": str(x_col).capitalize()},
                    "yaxis": {"title": str(y_col).capitalize()},
                    "template": "plotly_white",
                    "margin": {"l": 50, "r": 20, "t": 50, "b": 50}
                }
            }
