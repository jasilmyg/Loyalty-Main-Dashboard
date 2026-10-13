"""Data layer for the Text Sentiment page (/sentiment/), following SENTIMENT_ANALYSIS_GUIDE.md section 9.

The page reads reviews_scored.parquet, produced in batch by exports/refresh_sentiment.py, so nothing
is scored per page load (guide 9.1).  Sentiment here is the sentiment of the review TEXT; the star
rating is a separate dimension and is always labelled "High-rating" / "Low-rating" (guide section 2).
"""
import io
import os
import threading

import pandas as pd
from django.conf import settings

LABELS = ["Positive", "Neutral", "Negative", "Mixed", "No text / unreadable"]
DISPLAY = {"Neutral": "Neutral / unclear"}             # guide 9.4: label the Neutral bar "Neutral / unclear"
SMALL_GROUP = 500                                      # guide 9.3: grey out groups with fewer reviews
PAGE_SIZE = 25
FOOTER = "Sentiment by rule-based text analysis v{v}. Not validated against a human-labelled set."

LIMITATIONS = [
    "Malayalam script and heavy Manglish are only partly understood: such reviews drift into Neutral or Unreadable.",
    "Sarcasm and irony are not detected.",
    "Recall of Negative / Mixed has not been measured until a hand-labelled gold set exists, so the Negative count may be low.",
    "Reviews are grouped by star rating (Card), not by text. Use 'High-rating' for 4-5 star reviews and 'Positive' only for text sentiment.",
    "The old Sentiment, Emotion, Primary root cause and Suggested action columns are unreliable and are not used here.",
    "Duplicate texts (60+ characters) may be templated reviews, copying or an export artefact. Use the duplicate toggle.",
    "Theme keywords overlap and are rough; use them for direction, not exact counts.",
]

_lock = threading.Lock()
_cache = {"mtime": None, "df": None}


def parquet_path():
    p = getattr(settings, "SENTIMENT_PARQUET", None)
    if p:
        return str(p)
    return os.path.join(os.path.dirname(str(settings.BASE_DIR)), "exports", "reviews_scored.parquet")


def load():
    """Cached DataFrame, reloaded when the parquet file changes. None if it has not been generated yet."""
    path = parquet_path()
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    with _lock:
        if _cache["mtime"] != mtime:
            df = pd.read_parquet(path)
            df["Month"] = df["Date"].dt.to_period("M").dt.to_timestamp()
            df["sentiment_label"] = pd.Categorical(df["sentiment_label"], categories=LABELS)
            _cache.update(mtime=mtime, df=df)
        return _cache["df"]


def _disp(label):
    return DISPLAY.get(label, label)


def _share(n, total):
    return round(n / total * 100, 2) if total else 0.0


def apply_filters(df, params):
    for col, key in (("Card", "card"), ("RBM", "rbm"), ("BDM", "bdm"), ("Store", "store")):
        v = params.get(key, "all")
        if v and v != "all":
            df = df[df[col] == v]
    if params.get("dup") == "exclude":
        df = df[~df["dup_text"]]
    return df


def filter_options(df):
    return {
        "cards": sorted(df["Card"].dropna().unique().tolist()),
        "rbms": sorted(x for x in df["RBM"].dropna().unique() if x != "Unknown"),
        "bdms": sorted(x for x in df["BDM"].dropna().unique() if x != "Unknown"),
        "stores": sorted(df["Store"].dropna().unique().tolist()),
        "hierarchy": df[["RBM", "BDM", "Store"]].drop_duplicates().rename(
            columns={"RBM": "rbm", "BDM": "bdm", "Store": "store"}).to_dict("records"),
    }


def _meta(df_all, df):
    ver = str(df_all["sentiment_engine"].iloc[0]) if len(df_all) else "?"
    return {"engine": ver, "footer": FOOTER.format(v=ver), "total": int(len(df)), "all_total": int(len(df_all))}


def overview(df):
    total = len(df)
    counts = df["sentiment_label"].value_counts().reindex(LABELS, fill_value=0)
    dist = [{"label": _disp(l), "key": l, "count": int(counts[l]), "share": _share(int(counts[l]), total)} for l in LABELS]

    m = df.dropna(subset=["Month"]).groupby("Month", observed=True).agg(
        n=("sentiment_label", "size"), pos=("sentiment_label", lambda s: int((s == "Positive").sum()))).reset_index()
    m = m.sort_values("Month").tail(24)
    monthly = [{"month": r.Month.strftime("%b %y"), "reviews": int(r.n), "positive": int(r.pos),
                "positive_pct": _share(int(r.pos), int(r.n))} for r in m.itertuples()]

    rating_group = pd.cut(df["Rating"], bins=[0, 2, 3, 5], labels=["Low-rating (1-2★)", "Mid-rating (3★)", "High-rating (4-5★)"])
    ct = pd.crosstab(rating_group, df["sentiment_label"]).reindex(columns=LABELS, fill_value=0)
    compare = []
    for grp, row in ct.iterrows():
        tot = int(row.sum())
        compare.append({"group": str(grp), "total": tot,
                        "items": [{"label": _disp(l), "key": l, "count": int(row[l]), "share": _share(int(row[l]), tot)} for l in LABELS]})
    return {"distribution": dist, "monthly": monthly, "rating_vs_text": compare}


def stores(df):
    def agg(col):
        g = df.groupby(col, observed=True).agg(
            reviews=("sentiment_label", "size"),
            negative=("sentiment_label", lambda s: int((s == "Negative").sum())),
            mixed=("sentiment_label", lambda s: int((s == "Mixed").sum()))).reset_index()
        g = g[g[col].notna() & (g[col] != "Unknown")]
        g["neg_mixed"] = g.negative + g.mixed
        g["rate"] = (g.neg_mixed / g.reviews * 100).round(2)
        g["small"] = g.reviews < SMALL_GROUP
        g = g.sort_values(["small", "rate", "reviews"], ascending=[True, False, False])
        return [{"name": r[0], "reviews": int(r.reviews), "negative": int(r.negative), "mixed": int(r.mixed),
                 "neg_mixed": int(r.neg_mixed), "rate": float(r.rate), "small": bool(r.small)}
                for r in g.itertuples(index=False)]
    return {"stores": agg("Store"), "rbms": agg("RBM"), "bdms": agg("BDM"), "small_threshold": SMALL_GROUP}


def queue_frame(df, params):
    q = df[df["needs_review"]]
    label = params.get("label", "all")
    if label and label != "all":
        q = q[q["sentiment_label"] == label]
    term = (params.get("q") or "").strip()
    if term:
        q = q[q["Review"].str.contains(term, case=False, regex=False, na=False)]
    order = {"Negative": 0, "Mixed": 1, "Neutral": 2}
    q = q.assign(_o=q["sentiment_label"].astype(str).map(order).fillna(3)).sort_values(["_o", "Date"], ascending=[True, False])
    return q.drop(columns="_o")


def _row(r):
    return {"date": r.Date.strftime("%Y-%m-%d") if pd.notna(r.Date) else "", "store": r.Store, "rating": int(r.Rating),
            "label": _disp(str(r.sentiment_label)), "key": str(r.sentiment_label), "score": round(float(r.sentiment_score), 2),
            "review": r.Review[:600], "dup": bool(r.dup_text), "conflict": bool(r.rating_text_conflict), "card": r.Card}


def queue(df, params):
    q = queue_frame(df, params)
    try:
        page = max(int(params.get("page", 1)), 1)
    except ValueError:
        page = 1
    total = len(q)
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, pages)
    chunk = q.iloc[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    lab = q["sentiment_label"].value_counts()
    return {"total": int(total), "page": page, "pages": pages, "page_size": PAGE_SIZE,
            "by_label": [{"label": _disp(l), "key": l, "count": int(lab.get(l, 0))} for l in ("Negative", "Mixed", "Neutral")],
            "rows": [_row(r) for r in chunk.itertuples(index=False)]}


def quality(df):
    total = len(df)
    conf = df[df["rating_text_conflict"]]
    low_pos = conf[conf["Rating"] <= 2]
    high_neg = conf[conf["Rating"] >= 4]
    unread = int((df["sentiment_label"] == "No text / unreadable").sum())
    dups = int(df["dup_text"].sum())
    return {
        "conflicts": {"total": int(len(conf)), "share": _share(len(conf), total),
                      "low_rating_positive_text": int(len(low_pos)), "high_rating_negative_text": int(len(high_neg))},
        "duplicates": {"rows": dups, "share": _share(dups, total),
                       "distinct_texts": int(df[df["dup_text"]]["Review"].nunique())},
        "unreadable": {"rows": unread, "share": _share(unread, total)},
        "conflict_rows": {
            "low_rating_positive_text": [_row(r) for r in low_pos.sort_values("Date", ascending=False).head(20).itertuples(index=False)],
            "high_rating_negative_text": [_row(r) for r in high_neg.sort_values("Date", ascending=False).head(20).itertuples(index=False)],
        },
        "limitations": LIMITATIONS,
    }


def build(params):
    df_all = load()
    if df_all is None:
        return {"status": "missing",
                "message": "reviews_scored.parquet not found. Run `python refresh_sentiment.py` in the exports folder."}
    df = apply_filters(df_all, params)
    tab = params.get("tab", "overview")
    out = {"status": "ok", "tab": tab, "meta": _meta(df_all, df)}
    if params.get("filters") == "1":
        out["filters"] = filter_options(df_all)
    if len(df) == 0:
        out["empty"] = True
        return out
    if tab == "overview":
        out.update(overview(df))
    elif tab == "stores":
        out.update(stores(df))
    elif tab == "queue":
        out.update(queue(df, params))
    elif tab == "quality":
        out.update(quality(df))
    else:
        out = {"status": "error", "message": "Unknown tab"}
    return out


def export_queue(params):
    """xlsx bytes of the (filtered) review queue - guide 9.3 'export to Excel' button."""
    df_all = load()
    if df_all is None:
        return None
    q = queue_frame(apply_filters(df_all, params), params)
    cols = ["Date", "Store", "RBM", "BDM", "Rating", "Review", "Card", "sentiment_label", "sentiment_score",
            "rating_text_conflict", "dup_text"]
    out = q[cols].copy()
    out["Date"] = out["Date"].dt.date
    out["sentiment_label"] = out["sentiment_label"].astype(str)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as xw:
        out.to_excel(xw, sheet_name="Review Queue", index=False)
        ws = xw.sheets["Review Queue"]
        for i, w in enumerate([12, 28, 16, 16, 7, 90, 30, 20, 12, 14, 10]):
            ws.set_column(i, i, w)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, len(out), len(cols) - 1)
    return buf.getvalue()
