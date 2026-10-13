"""Rule-based sentiment engine for myG Google reviews.

Implements exports/SENTIMENT_ANALYSIS_GUIDE.md (VADER lexicon + domain rules, sentence/clause-level
decision, emoji handling, resolved-concern rule).  Run ``python sentiment_engine.py --selftest``.

Public API:  analyze(df, text_col, rating_col=None) -> DataFrame with the new columns
             sentiment_label, sentiment_score, needs_review, rating_text_conflict, sentiment_engine
"""
import re
import sys

__version__ = "1.0.0"

LABELS = ["Positive", "Neutral", "Negative", "Mixed", "No text / unreadable"]

SENTENCE_THRESHOLD = 0.30
DOCUMENT_THRESHOLD = 0.25
LONG_NEUTRAL_CHARS = 60          # Neutral texts at least this long go to the review queue

# ----------------------------------------------------------------------------------- lexicon edits
LEXICON_REMOVE = ["stop", "doubt", "doubts", "confused", "confusion", "low", "lower", "lowest",
                  "tight", "worried", "stress", "hidden", "costs"]
LEXICON_ADD = {
    # spelling variants / Manglish
    "exelent": 2.7, "excelent": 2.7, "excellant": 2.7, "exellent": 2.7, "gud": 1.9, "awsome": 3.1, "awesom": 3.1,
    "helpfull": 1.8, "nyc": 1.8, "supperb": 2.8, "superb": 2.8, "satisfy": 1.5, "satisfyed": 1.5,
    # words VADER misses that are clear complaints in retail reviews
    "delay": -1.6, "delayed": -1.6, "delays": -1.6, "delaying": -1.6,
    "unprofessional": -2.0, "irresponsible": -1.8, "arrogant": -1.8, "cheating": -2.4, "fraud": -2.6,
    "unhelpful": -1.8, "careless": -1.5, "negligent": -1.8, "overpriced": -1.6,
}

# ----------------------------------------------------------------------------------- rewrite rules
# (regex, replacement), applied IN ORDER: specific rules first, the generic negation rule last.
_COMPLAINT_NOUN = (r"delays?|delayed|queues?|waiting( time)?|wait|upselling|up-selling|hassles?|issues?|problems?|"
                   r"pressure|complaints?|stress|worr(?:y|ies)|confusion|trouble|difficult(?:y|ies)|cheating|"
                   r"hidden (?:charges?|costs?)")
REWRITE_RULES = [
    (r"\bone[- ]stop\b", "complete"),
    (r"\blowest price\b", "best price"),
    (r"\blow(?:er)? prices?\b", "good price"),
    (r"\bnot bad\b", "good"),
    (r"\b(?:never|do(?:es)? ?n[o']?t|don'?t|doesn'?t) disappoint(?:s|ed|ing)?\b", "very good"),
    (r"\bno (?:more )?doubts?\b", "very good"),
    (r"\b(?:fast|quick(?:ly)?)\b", "excellent"),
    # generic: no/without/zero + up to 2 (non-conjunction) words + complaint noun  ->  "smooth"
    (r"\b(?:no|without|zero|nil)\s+(?:(?!and\b|or\b|but\b)[A-Za-z]+\s+){0,2}(?:" + _COMPLAINT_NOUN + r")\b",
     "smooth process"),
]
_REWRITE = [(re.compile(p, re.I), r) for p, r in REWRITE_RULES]

# ----------------------------------------------------------------------------------- emoji
POS_EMOJI = set("👍❤🥰😍😊🙂😀😁😃😄👌🙏💯🔥⭐🌟💖💗💕👏😘🤩✅💝🎉✨😇🤗💙💚💛💜🧡😎👏🏻🏼🏽🏾🏿")
NEG_EMOJI = set("👎😡😠😤🤬😞😢😭💔😒🤮😖😣☹🙁😔❌👿😩😫")
_EMOJI_SKIP = set("🏻🏼🏽🏾🏿")      # skin-tone modifiers carry no polarity
POS_EMOJI -= _EMOJI_SKIP

_CONTRAST = re.compile(
    r"\.{2,}|[.!?]+(?:\s+|$)|[\n;]+|\b(?:but|however|although|though|except|unfortunately)\b", re.I)
_HEX = re.compile(r"^(?:[0-9a-fA-F]{2}){10,}$")
_LETTERS = re.compile(r"[A-Za-z]")

_sia = None


def _analyzer():
    global _sia
    if _sia is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _sia = SentimentIntensityAnalyzer()
        for w in LEXICON_REMOVE:
            _sia.lexicon.pop(w, None)
        for w, s in LEXICON_ADD.items():
            _sia.lexicon[w] = s
    return _sia


# ----------------------------------------------------------------------------------- steps
def preprocess(text):
    """literal '\\n' -> space; decode hex-encoded exports; keep only the '(Translated by Google)' part."""
    if text is None:
        return ""
    t = str(text)
    if t == "nan":
        return ""
    t = t.strip()
    if _HEX.match(t):                         # a few exports stored the comment as hex text
        try:
            t = bytes.fromhex(t).decode("utf-8", "ignore")
        except ValueError:
            pass
    t = t.replace("\\n", " ").replace("\n", " ")
    if "(Translated by Google)" in t:
        t = t.split("(Translated by Google)", 1)[1]
        t = t.split("(Original)", 1)[0]
    return re.sub(r"\s+", " ", t).strip()


def rewrite(text):
    t = re.sub(r"([A-Za-z])\1{2,}", r"\1\1", text)            # Gooood -> Good
    for rx, rep in _REWRITE:
        t = rx.sub(rep, t)
    return t


def _ascii(t):
    return t.encode("ascii", "ignore").decode("ascii")


def _emoji_counts(t):
    pos = sum(1 for ch in t if ch in POS_EMOJI)
    neg = sum(1 for ch in t if ch in NEG_EMOJI)
    return pos, neg


def classify(raw):
    """-> (label, score) for one review text."""
    t = preprocess(raw)
    pos_e, neg_e = _emoji_counts(t)
    if len(_LETTERS.findall(_ascii(t))) < 2:                  # 1. emoji decide
        if pos_e > neg_e:
            return "Positive", 0.5
        if neg_e > pos_e:
            return "Negative", -0.5
        return "No text / unreadable", 0.0
    sia = _analyzer()
    r = _ascii(rewrite(t))
    whole = sia.polarity_scores(r)["compound"]                # 2. score whole text and each clause
    clauses = [c.strip() for c in _CONTRAST.split(r) if c and len(_LETTERS.findall(c)) >= 2]
    cs = [sia.polarity_scores(c)["compound"] for c in clauses] or [whole]
    pos_idx = [i for i, s in enumerate(cs) if s >= SENTENCE_THRESHOLD]
    neg_idx = [i for i, s in enumerate(cs) if s <= -SENTENCE_THRESHOLD]
    if pos_idx and neg_idx:                                    # 3.
        if pos_idx[-1] > neg_idx[0] and cs[-1] >= SENTENCE_THRESHOLD:
            return "Positive", whole                           # concern resolved by the end
        return "Mixed", whole
    if neg_idx or whole <= -DOCUMENT_THRESHOLD:                # 4.
        return "Negative", whole
    if pos_idx or whole >= DOCUMENT_THRESHOLD:                 # 5.
        return "Positive", whole
    if pos_e > neg_e:                                          # 6.
        return "Positive", whole
    return "Neutral", whole                                    # 7.


# ----------------------------------------------------------------------------------- dataframe API
def analyze(df, text_col, rating_col=None):
    """Return a copy of df with sentiment columns added (scored once per distinct text)."""
    import pandas as pd
    out = df.copy()
    texts = out[text_col].fillna("").astype(str)
    cache = {}
    for t in texts.unique():
        cache[t] = classify(t)
    out["sentiment_label"] = texts.map(lambda t: cache[t][0])
    out["sentiment_score"] = texts.map(lambda t: round(cache[t][1], 4))
    cleaned_len = texts.map(lambda t: len(preprocess(t)))
    out["needs_review"] = (out.sentiment_label.isin(["Negative", "Mixed"]) |
                           ((out.sentiment_label == "Neutral") & (cleaned_len >= LONG_NEUTRAL_CHARS)))
    if rating_col is not None:
        r = pd.to_numeric(out[rating_col], errors="coerce")
        out["rating_text_conflict"] = (((r >= 4) & out.sentiment_label.isin(["Negative", "Mixed"])) |
                                       ((r <= 2) & (out.sentiment_label == "Positive")))
    out["sentiment_engine"] = __version__
    return out


# ----------------------------------------------------------------------------------- self tests
SELF_TESTS = [
    ("Budget was tight, but they helped me choose a great phone.", "Positive"),
    ("Very bad customer service", "Negative"),
    ("Good. Billing process ia too delay", "Mixed"),
    ("Purchased mobile", "Neutral"),
    ("*****", "No text / unreadable"),
    ("", "No text / unreadable"),
    ("Good\U0001F525\U0001F525\U0001F525", "Positive"),
    ("\U0001F44D", "Positive"),
    ("\U0001F44E", "Negative"),
    ("A one stop shop for all your electronics, very good", "Positive"),
    ("Great service, no unnecessary delays or upselling", "Positive"),
    ("I had doubts, but the team cleared them and was very helpful", "Positive"),
    ("They never disappoint", "Positive"),
    ("Exelent service", "Positive"),
    ("Gooood experience", "Positive"),
    ("Lowest price in town and great offers", "Positive"),
    ("Worst experience ever", "Negative"),
]


def selftest():
    bad = 0
    for text, want in SELF_TESTS:
        got = classify(text)[0]
        ok = got == want
        bad += not ok
        shown = text[:70].encode("ascii", "backslashreplace").decode("ascii")
        print(("PASS" if ok else "FAIL"), f"{want:22s} got {got:22s} | {shown!r}")
    print(f"{len(SELF_TESTS) - bad}/{len(SELF_TESTS)} passed")
    return bad == 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
