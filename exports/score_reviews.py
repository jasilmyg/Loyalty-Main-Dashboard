"""Score every review in myG_Google_Reviews_All_148140.xlsx with sentiment_engine and write a new workbook."""
import os, sys, re
import pandas as pd
import sentiment_engine as se

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "myG_Google_Reviews_All_148140.xlsx")
OUT = os.path.join(HERE, "myG_Google_Reviews_Sentiment_148140.xlsx")

print("reading", SRC, flush=True)
df = pd.read_excel(SRC, sheet_name="All Reviews")
print("rows", len(df), flush=True)

out = se.analyze(df, text_col="Review", rating_col="Rating")
out["dup_text"] = out.Review.fillna("").astype(str).str.len().ge(60) & out.duplicated("Review", keep=False)
out["hex_decoded"] = out.Review.fillna("").astype(str).str.match(r"^(?:[0-9a-fA-F]{2}){10,}$")
print(out.sentiment_label.value_counts(), flush=True)

order = se.LABELS
def pct(s): return (s / s.sum()).round(4)

# ---- summary tables
overall = out.sentiment_label.value_counts().reindex(order, fill_value=0).rename("Reviews").to_frame()
overall["Share"] = pct(overall["Reviews"])
by_card = pd.crosstab(out["Card"], out["sentiment_label"]).reindex(columns=order, fill_value=0)
by_card["Total"] = by_card.sum(axis=1)
by_rating = pd.crosstab(out["Rating"], out["sentiment_label"]).reindex(columns=order, fill_value=0)
by_rating["Total"] = by_rating.sum(axis=1)
facts = pd.DataFrame({
    "Metric": ["Total reviews", "Needs review (Negative, Mixed, long Neutral)", "Rating-text conflicts",
               "  of which 4-5 star but Negative/Mixed text", "  of which 1-2 star but Positive text",
               "Duplicate texts (60+ chars, rows)", "Hex-encoded comments decoded", "Engine version"],
    "Value": [len(out), int(out.needs_review.sum()), int(out.rating_text_conflict.sum()),
              int(((out.Rating >= 4) & out.sentiment_label.isin(["Negative", "Mixed"])).sum()),
              int(((out.Rating <= 2) & (out.sentiment_label == "Positive")).sum()),
              int(out.dup_text.sum()), int(out.hex_decoded.sum()), se.__version__],
})

# store-level Negative+Mixed rate (groups < 500 reviews are flagged, per the guide)
g = out.groupby("Store").agg(Reviews=("sentiment_label", "size"),
                              Negative=("sentiment_label", lambda s: (s == "Negative").sum()),
                              Mixed=("sentiment_label", lambda s: (s == "Mixed").sum())).reset_index()
g["Neg+Mixed"] = g.Negative + g.Mixed
g["Neg+Mixed rate"] = (g["Neg+Mixed"] / g.Reviews).round(4)
g["Small group (<500)"] = g.Reviews < 500
g = g.sort_values(["Small group (<500)", "Neg+Mixed rate"], ascending=[True, False])

cols = list(df.columns) + ["sentiment_label", "sentiment_score", "needs_review", "rating_text_conflict", "dup_text", "sentiment_engine"]
final = out[cols].copy()
final["Date"] = pd.to_datetime(final["Date"]).dt.date
queue = final[final.needs_review].sort_values(["sentiment_label", "Date"], ascending=[True, False])

print("writing", OUT, flush=True)
with pd.ExcelWriter(OUT, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as xw:
    wb = xw.book
    hdr = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F3A5F", "border": 1})
    wrap = wb.add_format({"text_wrap": True, "valign": "top"})
    top = wb.add_format({"valign": "top"})
    datef = wb.add_format({"num_format": "yyyy-mm-dd", "valign": "top"})
    pctf = wb.add_format({"num_format": "0.00%"})
    bold = wb.add_format({"bold": True})

    ws = wb.add_worksheet("Summary"); xw.sheets["Summary"] = ws
    ws.set_column(0, 0, 46); ws.set_column(1, 8, 16)
    ws.write(0, 0, "myG Google Reviews - text sentiment (rule-based, engine v%s)" % se.__version__, wb.add_format({"bold": True, "font_size": 14}))
    ws.write(1, 0, "Sentiment is of the review TEXT; Card / Rating describe the star rating. Not validated against a human-labelled set.")
    r = 3
    ws.write(r, 0, "Overall sentiment", bold); r += 1
    ov = overall.reset_index().rename(columns={"index": "Sentiment", "sentiment_label": "Sentiment"})
    for c, h in enumerate(["Sentiment", "Reviews", "Share"]): ws.write(r, c, h, hdr)
    for i, row in enumerate(ov.itertuples(index=False), start=r + 1):
        ws.write(i, 0, row[0]); ws.write_number(i, 1, row[1]); ws.write_number(i, 2, row[2], pctf)
    r += len(ov) + 3
    for title, tbl in (("Sentiment by card", by_card), ("Sentiment by star rating", by_rating)):
        ws.write(r, 0, title, bold); r += 1
        t = tbl.reset_index()
        for c, h in enumerate(t.columns): ws.write(r, c, str(h), hdr)
        for i, row in enumerate(t.itertuples(index=False), start=r + 1):
            for c, v in enumerate(row): ws.write(i, c, v if c == 0 and not isinstance(v, (int,)) else (int(v) if not isinstance(v, str) else v))
        r += len(t) + 3
    ws.write(r, 0, "Data-quality facts", bold); r += 1
    for c, h in enumerate(["Metric", "Value"]): ws.write(r, c, h, hdr)
    for i, row in enumerate(facts.itertuples(index=False), start=r + 1):
        ws.write(i, 0, row[0]); ws.write(i, 1, row[1])
    r += len(facts) + 3
    ws.write(r, 0, "Known limitations", bold); r += 1
    for line in ["Malayalam script / heavy Manglish is only partly understood: such reviews drift to Neutral or Unreadable.",
                 "Sarcasm is not detected. Recall of Negative/Mixed has not been measured (no gold set yet).",
                 "The old Sentiment / Emotion / Primary root cause columns come from earlier AI tagging and are unreliable.",
                 "Negative + Mixed are rare in 4-5 star reviews, so small stores (<500 reviews) swing on 1-2 reviews."]:
        ws.write(r, 0, line); r += 1

    def put(name, frame):
        frame.to_excel(xw, sheet_name=name, index=False)
        s = xw.sheets[name]
        for c, h in enumerate(frame.columns): s.write(0, c, h, hdr)
        widths = {"Date": 12, "Store": 28, "RBM": 16, "BDM": 16, "Rating": 7, "Reviewer": 20, "Review": 90, "Card": 30,
                  "Sub-category": 34, "Sentiment": 12, "Emotion": 16, "Primary root cause": 28, "Secondary issue": 28,
                  "Suggested action": 40, "sentiment_label": 18, "sentiment_score": 12, "needs_review": 12,
                  "rating_text_conflict": 14, "dup_text": 10, "sentiment_engine": 10}
        for c, h in enumerate(frame.columns):
            s.set_column(c, c, widths.get(h, 14), wrap if h == "Review" else (datef if h == "Date" else top))
        s.freeze_panes(1, 0); s.autofilter(0, 0, len(frame), len(frame.columns) - 1)

    put("All Reviews", final)
    put("Review Queue", queue)
    store_ws_name = "Store Sentiment"
    g.to_excel(xw, sheet_name=store_ws_name, index=False)
    s = xw.sheets[store_ws_name]
    for c, h in enumerate(g.columns): s.write(0, c, h, hdr)
    s.set_column(0, 0, 32); s.set_column(1, 6, 16); s.freeze_panes(1, 0)

print("saved", OUT, os.path.getsize(OUT) // 1024, "KB", flush=True)
print("queue rows", len(queue), flush=True)
