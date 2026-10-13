# Sentiment Analysis Guideline - myG Google Reviews Dashboard

Version 1.0.0 | Companion code: `sentiment\_engine.py` | Data: `myG\_Google\_Reviews\_RCA.xlsx`

This guide documents the sentiment method used to re-analyse the **Positive and Negative Reviews**  and explains how to wire it into a Python dashboard. Everything in `sentiment\_engine.py` is explained here, and the numbers quoted come from running that file on the 142,068 rows.

\---

## 1\. Scope and design decisions

|Decision|Choice|Reason|
|-|-|-|
|Approach|Rule-based (VADER lexicon + domain rules)|Fast (about 15 s for 142k rows on CPU), explainable, no model download, works offline|
|Granularity|One label per review, decided sentence by sentence|Detects "praise, then complaint" reviews that a whole-text score hides|
|Output|5 labels + a score|Retail reviews are often mixed or unreadable; forcing 3 classes hides that|
|Language|English, Manglish spellings, Google-translated text|Malayalam-script text is only partly understood (see section 8)|

**Not in scope:** topic or root-cause tagging (the sheet's `Primary root cause` column is unreliable, see section 8), and any claim about customers' true satisfaction beyond the review text.

\---

## 2\. Two separate dimensions - keep them apart in the dashboard

The source workbook splits reviews by **star rating**, not by text. The Summary sheet states the rule: 1-2 star (or a 3 star with a clear complaint) goes to Issue Reviews; everything else goes to Positive \& General Feedback.

|Dimension|Column|Meaning|
|-|-|-|
|Rating bucket|`Rating`, sheet name, `Card`|What the customer clicked|
|Text sentiment|`sentiment\_label` (this guide)|What the customer wrote|

They disagree often enough to matter: 4-5 star reviews that contain complaints sit in the Positive sheet, and some 1-2 star reviews say "Good". The engine adds `rating\_text\_conflict` to surface these (288 rows in the Positive sheet).

**Rule for the dashboard:** never label a chart "positive reviews" when it is really "4-5 star reviews". Use "High-rating reviews" for the sheet and "Positive" only for `sentiment\_label`.

\---

## 3\. Label definitions

|Label|Definition|Example|
|-|-|-|
|**Positive**|Praise only, or a concern that is resolved by the end|"Budget was tight, but they helped me choose a great phone."|
|**Negative**|Complaint only, no praise|"Very bad customer service"|
|**Mixed**|Praise followed by a complaint|"Good. Billing process ia too delay"|
|**Neutral**|Text present but no clear sentiment|"Purchased mobile"|
|**No text / unreadable**|Empty, symbols only, or emoji with no polarity|"\*\*\*\*\*", empty cell|

Emoji-only reviews: positive emoji (👍 ❤ 🥰 ...) give Positive, negative emoji (👎 😡 ...) give Negative.

\---

## 4\. Pipeline

```
raw Review
  1. preprocess()     literal "\\n" -> space; keep only the "(Translated by Google)" part
  2. REWRITE\_RULES    fix phrases the lexicon misreads ("no delays", "not bad", "never disappoint" ...)
  3. split            into sentences and contrast clauses (. ! ? but however although except unfortunately)
  4. score            VADER compound score per clause and for the whole text (ASCII only, emoji stripped)
  5. decide           label by the order in section 6
  6. flags            needs\_review, rating\_text\_conflict
```

\---

## 5\. Lexicon and rewrite rules (the part you tune)

### 5.1 Why VADER needs edits

VADER is built for social media in general English. On this data it made predictable mistakes, found by auditing flagged reviews:

|Problem seen|Cause|Fix|
|-|-|-|
|"A one stop shop..." scored negative|"stop" is a negative word|Remove `stop` from lexicon; rewrite "one-stop"|
|"Good🔥🔥🔥" scored negative|emoji "fire" read as a negative word|Strip non-ASCII before scoring; handle emoji separately|
|"no unnecessary delays or upselling" scored negative|negation ignored across several words|Generic rule: no/without/zero + up to 2 words + complaint noun becomes "smooth"|
|"had doubts, the team cleared them" scored negative|"doubts" is negative|Remove `doubt(s)`, `confused`, `confusion`|
|"never disappoint" scored negative|"disappoint" is negative|Rewrite to "very good"|
|"Exelent", "Gooood", "Gud" scored neutral|spelling variants not in the lexicon|Add spellings with scores|
|"lowest price" scored negative|"low" is negative|Remove `low/lower/lowest`; rewrite "lowest price" to "best price"|

### 5.2 How to add or change a rule

1. Put the review text in `SELF\_TESTS` with the label it should get. Run `python sentiment\_engine.py --selftest`; it must fail.
2. Add a lexicon edit (`LEXICON\_ADD` / `LEXICON\_REMOVE`) or a `REWRITE\_RULES` entry.
3. Re-run the self-tests **and** the gold-set check (section 7). Rules fix one case and can break another.
4. Bump `\_\_version\_\_` and add a line to the change log (section 11).

Rules run **in order**. The specific rules come before the generic negation rule on purpose.

### 5.3 Rule hazards to know about

* `fast` and `quick(ly)` are rewritten to "excellent". This is right for "fast processing" and wrong for "didn't respond quickly". Review this rule if you see delivery or response-time complaints scored Positive.
* Neutralised words (`tight`, `worried`, `stress`, `hidden`, `costs`) show up in both praise and complaints. Neutralising them avoids false negatives but can miss a real complaint that relies on them alone.

\---

## 6\. Decision logic (in order)

Thresholds are constants at the top of the file: `SENTENCE\_THRESHOLD = 0.30`, `DOCUMENT\_THRESHOLD = 0.25`.

1. Fewer than 2 letters: emoji decide (`Positive`, `Negative`), otherwise `No text / unreadable`.
2. Score the whole text and each clause.
3. If a clause is positive (>= 0.30) **and** another is negative (<= -0.30):

   * the last positive clause comes after the first negative one **and** the final clause is positive: `Positive` (concern resolved)
   * otherwise: `Mixed`
4. Else if any clause is negative, or the whole text is <= -0.25: `Negative`.
5. Else if any clause is positive, or the whole text is >= 0.25: `Positive`.
6. Else if positive emoji outnumber negative emoji: `Positive`.
7. Else `Neutral`.

\---

## 7\. Validation - how you know it works

The rules are only as good as the evidence. Do this before you publish numbers to stakeholders.

### 7.1 Build a gold set

Label about 500 reviews by hand (two people if possible; resolve disagreements). **Do not sample at random**: only about 0.2% of this sheet is Negative or Mixed, so a random 500 holds one or two. Use a stratified sample:

|Stratum|Rows|
|-|-|
|Engine says Negative|100|
|Engine says Mixed|100|
|Engine says Neutral|100|
|Engine says Positive and the text has a complaint keyword (parking, price, delay, rude, but, ...)|100|
|Engine says Positive, plain random|50|
|No text / unreadable|25|
|3-star rows|25|

The keyword-enriched stratum is what measures **recall** for complaints, which I have not measured so far.

### 7.2 Score it

```python
import pandas as pd
from sentiment\_engine import analyze, LABELS

gold = pd.read\_csv("gold\_labels.csv")            # columns: Review, gold\_label
pred = analyze(gold, "Review")

print("accuracy:", (pred.sentiment\_label == gold.gold\_label).mean())
print(pd.crosstab(gold.gold\_label, pred.sentiment\_label))      # rows = truth, cols = engine
for lab in LABELS:
    tp = ((pred.sentiment\_label == lab) \& (gold.gold\_label == lab)).sum()
    precision = tp / max((pred.sentiment\_label == lab).sum(), 1)
    recall = tp / max((gold.gold\_label == lab).sum(), 1)
    print(f"{lab:22s} precision {precision:.2f}  recall {recall:.2f}")
```

Because the strata are not proportional to the data, treat these as per-class precision and recall, not an overall accuracy for the whole sheet.

### 7.3 Suggested acceptance targets (my suggestion, not measured)

* Negative and Mixed precision of 0.85 or better (current audit: about 0.90 for Negative by reading the distinct texts).
* Negative recall of 0.80 or better on the keyword-enriched stratum.
* 100% of `SELF\_TESTS` passing on every change.

### 7.4 Reference results on this dataset (v1.0.0)

Use these to catch accidental changes: if a rule edit moves them a lot, find out why.

|Sentiment|Reviews|Share|
|-|-|-|
|Positive|137,445|96.7%|
|Neutral|4,079|2.9%|
|Negative|159|0.11%|
|Mixed|148|0.10%|
|No text / unreadable|237|0.17%|

\---

## 8\. Known limitations (state these in the dashboard)

1. **Malayalam script and Manglish.** Only English words are scored. Reviews in Malayalam script or heavy Manglish drift into Neutral or Unreadable. Some of the 4,079 Neutral rows are probably Positive; about 45% of the *sheet's own* Neutral label read as Positive to this engine.
2. **Sarcasm and irony** are not detected.
3. **Recall is not measured** until you build the gold set. The Negative count may be low.
4. **Rating-based sheet split.** See section 2.
5. **The sheet's own `Sentiment`, `Emotion`, `Primary root cause` and `Suggested action` columns are unreliable.** About 65% of its Negative rows read as Positive or Neutral here, and root causes like "Pricing \& High Cost" appear on clearly happy reviews. Do not chart them as facts.
6. **Duplicate text.** 4,193 distinct texts of 60+ characters appear more than once, covering 17,520 rows. The cause is unknown (templated reviews, copying, or an export artefact). Add a flag and let users toggle it:

```python
   df\["dup\_text"] = df.Review.fillna("").str.len().ge(60) \& df.duplicated("Review", keep=False)
   ```

7. **Theme keywords** (staff, pricing, delay...) overlap and are rough; use them for direction, not exact counts.

\---

## 9\. Dashboard integration

### 9.1 Compute once, not per page load

Scoring 142k rows takes about 15 seconds. Run it as a batch step when the data refreshes and store the result.

```python
# refresh\_sentiment.py  (run on each data refresh)
import pandas as pd
from sentiment\_engine import analyze

df = pd.read\_excel("myG\_Google\_Reviews\_RCA.xlsx", sheet\_name="Positive Reviews")
out = analyze(df, text\_col="Review", rating\_col="Rating")
out\["dup\_text"] = out.Review.fillna("").str.len().ge(60) \& out.duplicated("Review", keep=False)
out.to\_parquet("reviews\_scored.parquet", index=False)      # needs pyarrow
```

If you add the Issue Reviews sheet, run it the same way and concatenate with a `source\_sheet` column.

### 9.2 Columns the dashboard should read

|Column|Use|
|-|-|
|`sentiment\_label`, `sentiment\_score`|filters, KPIs, histograms|
|`needs\_review`|the review queue (Negative, Mixed, and long Neutral texts)|
|`rating\_text\_conflict`|"stars vs text disagree" panel|
|`dup\_text`|duplicate toggle|
|`sentiment\_engine`|version shown in the footer, so numbers can be traced|
|`Store`, `RBM`, `BDM`, `Date`, `Rating`|slicers|

### 9.3 Suggested pages

1. **Overview:** sentiment share (bar), monthly Positive %, 4-5 star vs text-sentiment comparison.
2. **Stores and RBMs:** Negative + Mixed rate per store/RBM. **Hide or grey out groups with fewer than about 500 reviews**; at a 0.2% base rate small stores swing on one or two reviews.
3. **Review queue:** table of `needs\_review` rows with search, store filter and an "export to Excel" button.
4. **Data quality:** `rating\_text\_conflict` rows, duplicate-text rate, share of Unreadable, and a note listing section 8.


```

### 9.4 Display rules

* Show **counts next to percentages**. "0.1%" of a small group is a handful of reviews.
* Label the Neutral bar "Neutral / unclear", since much of it is short or non-English praise.
* Footer text: "Sentiment by rule-based text analysis v1.0.0. Not validated against a human-labelled set." Remove the last sentence only after you complete section 7.

\---

## 10\. Upgrade path (when rules stop being enough)

Rules hit a ceiling on Malayalam, Manglish and sarcasm. The usual next step is a **hybrid**: keep the rules as the baseline and send only uncertain rows (Neutral with long text, Mixed, rating-text conflicts, non-ASCII heavy rows) to a multilingual model.

* Candidates to evaluate: multilingual transformer sentiment models (for example XLM-RoBERTa based ones) and Indian-language models such as MuRIL or IndicBERT. I have not tested any of them on this data, so check licence, size and Malayalam coverage first.
* Accept a model only if it beats the rules on your **gold set** (section 7) for the same classes. Do not switch on a leaderboard score alone.
* Keep `sentiment\_engine` in the output column so you can compare versions side by side.

\---

## 11\. Maintenance checklist and change log

On every change: update `SELF\_TESTS`, run `--selftest`, re-score the gold set, compare section 7.4 numbers, bump `\_\_version\_\_`, and add a line below.

|Version|Change|
|-|-|
|1.0.0|Sentence-level logic; negation rewrite rules (no delays / no queues / without upselling); emoji handling; resolved-concern rule; lexicon edits for Manglish spellings and false-negative words. Self-tests 14/14.|

\---

```

