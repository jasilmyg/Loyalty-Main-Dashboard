"""Batch refresh (SENTIMENT_ANALYSIS_GUIDE.md section 9.1): score every review once and store the result.

Run on each data refresh:   python refresh_sentiment.py
Input : myG_Google_Reviews_All.xlsx  (sheet "All Reviews")
Output: reviews_scored.parquet              (read by the dashboard page /sentiment/)

The old Sentiment / Emotion / Primary root cause / Secondary issue / Suggested action columns are
dropped on purpose: the guide (section 8, point 5) says they are unreliable and must not be charted.
"""
import os
import pandas as pd
from sentiment_engine import analyze

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "myG_Google_Reviews_All.xlsx")
OUT = os.path.join(HERE, "reviews_scored.parquet")

df = pd.read_excel(SRC, sheet_name="All Reviews")
out = analyze(df, text_col="Review", rating_col="Rating")
out["dup_text"] = out.Review.fillna("").astype(str).str.len().ge(60) & out.duplicated("Review", keep=False)
out["Date"] = pd.to_datetime(out["Date"], errors="coerce")

keep = ["Date", "Store", "RBM", "BDM", "Rating", "Review", "Card", "Sub-category", "sentiment_label",
        "sentiment_score", "needs_review", "rating_text_conflict", "dup_text", "sentiment_engine"]
out = out[keep]
out["Review"] = out["Review"].fillna("").astype(str)
out.to_parquet(OUT, index=False)
print("saved", OUT, len(out), "rows")
print(out.sentiment_label.value_counts().to_string())
