import os, sys, time, json
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'myg_loyalty_dashboard.settings'
import django; django.setup()
from analytics.clickhouse_service import get_ch_client
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from sklearn.cluster import KMeans
from sklearn.pipeline import Pipeline
import warnings
warnings.filterwarnings('ignore')

client = get_ch_client()

TODAY = '2026-08-14'
JAS26_START = '2026-07-01'
JAS25_S, JAS25_E = '2025-07-01', '2025-09-30'
JAS24_S, JAS24_E = '2024-07-01', '2024-09-30'
AMJ26_S, AMJ26_E = '2026-04-01', '2026-06-30'
AMJ25_S, AMJ25_E = '2025-04-01', '2025-06-30'

EXCL_JAS26 = f"""
    customer_mobile NOT IN (
        SELECT DISTINCT customer_mobile FROM azure_invoice_report
        WHERE toDate(date) >= toDate('{JAS26_START}')
          AND toDate(date) <= toDate('{TODAY}') AND invoice_total > 0
    )
"""

print("=" * 65)
print("AI CUSTOMER TARGETING ENGINE — JAS 2026")
print("=" * 65)
print()

# ─────────────────────────────────────────────────────────────────
# STEP 1: Fetch training features (features as of AMJ 2025 end → predict JAS 2025)
# ─────────────────────────────────────────────────────────────────
print("STEP 1: Fetching training dataset (features up to Jun 30 2025)...")
t0 = time.time()

train_q = '''
    SELECT
        customer_mobile,
        count() AS freq,
        round(sum(invoice_total), 2) AS monetary,
        round(avg(invoice_total), 2) AS avg_spend,
        round(max(invoice_total), 2) AS max_spend,
        dateDiff('day', toDate(min(date)), toDate('2025-07-01')) AS tenure_days,
        dateDiff('day', toDate(max(date)), toDate('2025-07-01')) AS recency_days,
        countIf(toDate(date) BETWEEN toDate('2024-07-01') AND toDate('2024-09-30')) AS jas24_count,
        countIf(toDate(date) BETWEEN toDate('2025-04-01') AND toDate('2025-06-30')) AS amj_q_count,
        countIf(toMonth(date) IN (7,8,9)) AS jas_season_total,
        countIf(invoice_total > 50000) AS big_ticket_count,
        countIf(financier_name != '') AS financed_count
    FROM azure_invoice_report
    WHERE toDate(date) < toDate('2025-07-01')
      AND toDate(date) != toDate('1970-01-01')
      AND invoice_total > 0
      AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
    GROUP BY customer_mobile
    HAVING freq >= 1
    LIMIT 300000
'''

r_train = client.query(train_q)
cols_train = r_train.column_names
df_train = pd.DataFrame(r_train.result_rows, columns=cols_train)
print(f"  Training features: {len(df_train):,} customers  ({time.time()-t0:.1f}s)")

# ─────────────────────────────────────────────────────────────────
# STEP 2: Get JAS 2025 labels (who actually bought in JAS 2025)
# ─────────────────────────────────────────────────────────────────
print("STEP 2: Fetching JAS 2025 labels...")
t0 = time.time()
r_label = client.query(f'''
    SELECT DISTINCT customer_mobile FROM azure_invoice_report
    WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
      AND invoice_total > 0 AND length(customer_mobile) = 10
      AND customer_mobile NOT IN ('1313131313','0000000000','9999999999')
''')
jas25_buyers = set(row[0] for row in r_label.result_rows)
df_train['label'] = df_train['customer_mobile'].isin(jas25_buyers).astype(int)
pos_rate = df_train['label'].mean()
print(f"  JAS 2025 buyers: {len(jas25_buyers):,}  Positive rate: {pos_rate:.1%}  ({time.time()-t0:.1f}s)")

# ─────────────────────────────────────────────────────────────────
# STEP 3: Feature engineering
# ─────────────────────────────────────────────────────────────────
print("STEP 3: Engineering features...")
df_train['purchase_velocity'] = df_train['freq'] / df_train['tenure_days'].clip(1)
df_train['jas_rate'] = df_train['jas_season_total'] / df_train['freq'].clip(1)
df_train['finance_rate'] = df_train['financed_count'] / df_train['freq'].clip(1)
df_train['recency_score'] = 1.0 / (df_train['recency_days'].clip(1) / 30.0)
df_train['high_value_flag'] = (df_train['avg_spend'] > 20000).astype(int)
df_train['recent_active'] = (df_train['amj_q_count'] > 0).astype(int)
df_train['jas24_active'] = (df_train['jas24_count'] > 0).astype(int)

FEATURES = ['freq', 'monetary', 'avg_spend', 'max_spend', 'tenure_days',
            'recency_days', 'jas24_count', 'amj_q_count', 'jas_season_total',
            'big_ticket_count', 'financed_count', 'purchase_velocity',
            'jas_rate', 'finance_rate', 'recency_score',
            'high_value_flag', 'recent_active', 'jas24_active']

X = df_train[FEATURES].fillna(0).replace([np.inf, -np.inf], 0).values
y = df_train['label'].values

# ─────────────────────────────────────────────────────────────────
# STEP 4: Train ensemble model
# ─────────────────────────────────────────────────────────────────
print("STEP 4: Training ensemble model (RF + GBM + MLP)...")
t0 = time.time()

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

scaler = StandardScaler()
X_train_sc = scaler.fit_transform(X_train)
X_test_sc = scaler.transform(X_test)

# Random Forest
rf = RandomForestClassifier(n_estimators=200, max_depth=10, min_samples_leaf=20,
                             n_jobs=-1, random_state=42, class_weight='balanced')
rf.fit(X_train, y_train)
rf_prob_train = rf.predict_proba(X_train)[:, 1]
rf_prob_test = rf.predict_proba(X_test)[:, 1]
print(f"  RF done: accuracy={accuracy_score(y_test, rf.predict(X_test)):.3f}")

# Gradient Boosting
gbm = GradientBoostingClassifier(n_estimators=150, max_depth=6, learning_rate=0.05,
                                  subsample=0.8, random_state=42)
gbm.fit(X_train, y_train)
gbm_prob_train = gbm.predict_proba(X_train)[:, 1]
gbm_prob_test = gbm.predict_proba(X_test)[:, 1]
print(f"  GBM done: accuracy={accuracy_score(y_test, gbm.predict(X_test)):.3f}")

# MLP Neural Network (Deep Learning component)
mlp = MLPClassifier(hidden_layer_sizes=(128, 64, 32), activation='relu',
                    solver='adam', alpha=0.001, batch_size=512,
                    learning_rate_init=0.001, max_iter=50, random_state=42,
                    early_stopping=True, validation_fraction=0.1)
mlp.fit(X_train_sc, y_train)
mlp_prob_test = mlp.predict_proba(X_test_sc)[:, 1]
print(f"  MLP done: accuracy={accuracy_score(y_test, mlp.predict(X_test_sc)):.3f}")

# Ensemble: weighted average
ensemble_prob_test = 0.35 * rf_prob_test + 0.40 * gbm_prob_test + 0.25 * mlp_prob_test
ensemble_pred = (ensemble_prob_test > 0.5).astype(int)
acc = accuracy_score(y_test, ensemble_pred)
prec = precision_score(y_test, ensemble_pred)
rec = recall_score(y_test, ensemble_pred)
f1 = f1_score(y_test, ensemble_pred)

print(f"\n  ENSEMBLE METRICS:")
print(f"    Accuracy : {acc:.3f}")
print(f"    Precision: {prec:.3f}")
print(f"    Recall   : {rec:.3f}")
print(f"    F1       : {f1:.3f}")
print(f"  Training time: {time.time()-t0:.1f}s")

# Feature importance from RF + GBM
fi_rf  = rf.feature_importances_
fi_gbm = gbm.feature_importances_
fi_combined = 0.5 * fi_rf + 0.5 * fi_gbm
fi_sorted = sorted(zip(FEATURES, fi_combined.tolist()), key=lambda x: -x[1])

# ─────────────────────────────────────────────────────────────────
# STEP 5: Score the TARGET pool — JAS 2025 buyers NOT in JAS 2026
# ─────────────────────────────────────────────────────────────────
print("\nSTEP 5: Fetching prediction features (as of Aug 14, 2026 for JAS 2026)...")
t0 = time.time()

pred_q = f'''
    SELECT
        a.customer_mobile,
        a.branch,
        count() AS freq,
        round(sum(a.invoice_total), 2) AS monetary,
        round(avg(a.invoice_total), 2) AS avg_spend,
        round(max(a.invoice_total), 2) AS max_spend,
        dateDiff('day', toDate(min(a.date)), toDate('{TODAY}')) AS tenure_days,
        dateDiff('day', toDate(max(a.date)), toDate('{TODAY}')) AS recency_days,
        countIf(toDate(a.date) BETWEEN toDate('{JAS24_S}') AND toDate('{JAS24_E}')) AS jas24_count,
        countIf(toDate(a.date) BETWEEN toDate('{AMJ26_S}') AND toDate('{AMJ26_E}')) AS amj26_count,
        countIf(toMonth(a.date) IN (7,8,9)) AS jas_season_total,
        countIf(a.invoice_total > 50000) AS big_ticket_count,
        countIf(a.financier_name != '') AS financed_count,
        max(a.financier_name) AS last_financier,
        toString(max(toDate(a.date))) AS last_purchase_date
    FROM azure_invoice_report a
    WHERE length(a.customer_mobile) = 10
      AND a.customer_mobile NOT IN ('1313131313','0000000000','9999999999')
      AND a.invoice_total > 0
      AND toDate(a.date) != toDate('1970-01-01')
      -- ONLY customers who bought in JAS 2025 but NOT yet in JAS 2026
      AND a.customer_mobile IN (
          SELECT DISTINCT customer_mobile FROM azure_invoice_report
          WHERE toDate(date) BETWEEN toDate('{JAS25_S}') AND toDate('{JAS25_E}')
            AND invoice_total > 0
      )
      AND {EXCL_JAS26}
    GROUP BY a.customer_mobile, a.branch
    ORDER BY max_spend DESC
    LIMIT 100000
'''

r_pred = client.query(pred_q)
cols_pred = r_pred.column_names
df_pred = pd.DataFrame(r_pred.result_rows, columns=cols_pred)
print(f"  Prediction pool: {len(df_pred):,} customers  ({time.time()-t0:.1f}s)")

# ─────────────────────────────────────────────────────────────────
# STEP 6: Feature engineering for prediction set
# ─────────────────────────────────────────────────────────────────
print("STEP 6: Scoring customers...")
t0 = time.time()

df_pred['purchase_velocity'] = df_pred['freq'] / df_pred['tenure_days'].clip(1)
df_pred['jas_rate'] = df_pred['jas_season_total'] / df_pred['freq'].clip(1)
df_pred['finance_rate'] = df_pred['financed_count'] / df_pred['freq'].clip(1)
df_pred['recency_score'] = 1.0 / (df_pred['recency_days'].clip(1) / 30.0)
df_pred['high_value_flag'] = (df_pred['avg_spend'] > 20000).astype(int)
df_pred['recent_active'] = (df_pred['amj26_count'] > 0).astype(int)
df_pred['jas24_active'] = (df_pred['jas24_count'] > 0).astype(int)
df_pred.rename(columns={'amj26_count': 'amj_q_count'}, inplace=True)

X_pred_raw = df_pred[FEATURES].fillna(0).replace([float('inf'), float('-inf')], 0).values

X_pred_sc = scaler.transform(X_pred_raw)

# Ensemble score
rf_s  = rf.predict_proba(X_pred_raw)[:, 1]
gbm_s = gbm.predict_proba(X_pred_raw)[:, 1]
mlp_s = mlp.predict_proba(X_pred_sc)[:, 1]
df_pred['score'] = (0.35 * rf_s + 0.40 * gbm_s + 0.25 * mlp_s) * 100

print(f"  Score distribution:")
print(f"    > 80%: {(df_pred['score'] > 80).sum():,} customers")
print(f"    > 60%: {(df_pred['score'] > 60).sum():,} customers")
print(f"    > 40%: {(df_pred['score'] > 40).sum():,} customers")
print(f"  Scoring time: {time.time()-t0:.1f}s")

# ─────────────────────────────────────────────────────────────────
# STEP 7: K-Means Customer Segmentation
# ─────────────────────────────────────────────────────────────────
print("\nSTEP 7: Segmenting customers (K-Means, 5 clusters)...")
t0 = time.time()
seg_features = ['freq','monetary','recency_days','jas_season_total','amj_q_count']
X_seg = df_pred[seg_features].fillna(0).replace([np.inf,-np.inf],0).values
X_seg_sc = StandardScaler().fit_transform(X_seg)

km = KMeans(n_clusters=5, random_state=42, n_init=10)
df_pred['cluster'] = km.fit_predict(X_seg_sc)

# Label clusters by their characteristics
cluster_stats = df_pred.groupby('cluster').agg(
    avg_score=('score','mean'),
    avg_freq=('freq','mean'),
    avg_recency=('recency_days','mean'),
    avg_jas=('jas_season_total','mean'),
    avg_amj26=('amj_q_count','mean'),
    count=('customer_mobile','count')
).reset_index()

# Assign segment names based on cluster characteristics
def segment_name(row):
    if row['avg_score'] > 65 and row['avg_jas'] > 1.5:
        return 'JAS Champions'
    elif row['avg_amj26'] > 0.5 and row['avg_freq'] > 3:
        return 'Active Loyalists'
    elif row['avg_score'] > 50 and row['avg_recency'] < 180:
        return 'Potential Returners'
    elif row['avg_freq'] > 5 and row['avg_recency'] < 365:
        return 'Seasonal Buyers'
    else:
        return 'Inactive High-Value'

cluster_stats['segment'] = cluster_stats.apply(segment_name, axis=1)
cluster_map = dict(zip(cluster_stats['cluster'], cluster_stats['segment']))
df_pred['segment'] = df_pred['cluster'].map(cluster_map)
print(f"  Segments: {cluster_stats[['segment','count','avg_score']].to_string(index=False)}")
print(f"  Segmentation time: {time.time()-t0:.1f}s")

# ─────────────────────────────────────────────────────────────────
# STEP 8: Save results
# ─────────────────────────────────────────────────────────────────
print("\nSTEP 8: Saving scored customer list...")

df_sorted = df_pred.sort_values('score', ascending=False).reset_index(drop=True)

top_customers = []
for i, row in df_sorted.head(5000).iterrows():
    mob = str(row['customer_mobile'])
    score = float(row['score'])
    if score >= 70:
        action = 'Call TODAY — Very High Probability'
    elif score >= 55:
        action = 'Call this week — High Probability'
    elif score >= 40:
        action = 'SMS + WhatsApp — Moderate Probability'
    else:
        action = 'Email campaign — Low-Medium Probability'

    top_customers.append({
        'rank': int(df_sorted.index.get_loc(i) + 1),
        'mobile_masked': mob[:3] + 'XXXXX' + mob[-3:],
        'mobile_full': mob,
        'branch': str(row['branch']),
        'score': round(score, 1),
        'segment': str(row['segment']),
        'last_purchase': str(row['last_purchase_date']),
        'total_purchases': int(row['freq']),
        'total_spend': float(row['monetary']),
        'avg_spend': float(row['avg_spend']),
        'recency_days': int(row['recency_days']),
        'jas_history': int(row['jas_season_total']),
        'amj26_bought': bool(row['amj_q_count'] > 0),
        'action': action,
        'last_financier': str(row['last_financier']) if str(row['last_financier']) else 'Cash',
    })

# Score distribution for histogram
score_bins = [0, 20, 30, 40, 50, 60, 70, 80, 90, 100]
score_hist = []
for i in range(len(score_bins)-1):
    lo, hi = score_bins[i], score_bins[i+1]
    cnt = int(((df_pred['score'] >= lo) & (df_pred['score'] < hi)).sum())
    score_hist.append({'range': f'{lo}-{hi}%', 'count': cnt})

# Segment summary
seg_summary = []
for _, row in cluster_stats.iterrows():
    seg_name = cluster_map[row['cluster']]
    seg_customers = df_pred[df_pred['cluster'] == row['cluster']]
    seg_summary.append({
        'segment': seg_name,
        'count': int(row['count']),
        'avg_score': round(float(row['avg_score']), 1),
        'avg_freq': round(float(row['avg_freq']), 1),
        'avg_recency': round(float(row['avg_recency']), 0),
        'expected_conversions': int(seg_customers['score'].sum() / 100),
    })

output = {
    'generated_at': TODAY,
    'model': {
        'type': 'Ensemble (Random Forest + Gradient Boosting + Neural Network)',
        'accuracy': round(acc, 4),
        'precision': round(prec, 4),
        'recall': round(rec, 4),
        'f1': round(f1, 4),
        'training_samples': len(X_train),
        'test_samples': len(X_test),
        'features_used': len(FEATURES),
        'training_label': 'JAS 2025 repeat purchase'
    },
    'target_kpis': {
        'quarter_target': 529364,
        'achieved': 186870,
        'gap': 342494,
        'days_left': 47,
        'jas25_not_jas26': 685104,
        'jas24_not_jas26': 545919,
        'jas_loyalists': 69728,
        'amj26_not_jas26': 529426,
        'hottest_leads': 65910
    },
    'score_summary': {
        'total_scored': len(df_pred),
        'high_prob_80plus': int((df_pred['score'] > 80).sum()),
        'high_prob_60plus': int((df_pred['score'] > 60).sum()),
        'expected_conversions_top5000': int(sum(c['score'] for c in top_customers) / 100),
    },
    'feature_importance': [
        {'feature': f, 'importance': round(i * 100, 2)}
        for f, i in fi_sorted[:15]
    ],
    'score_distribution': score_hist,
    'segment_summary': seg_summary,
    'top_customers': top_customers,
}

with open('analytics/ai_targeting_scores.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\nSaved {len(top_customers):,} customers to analytics/ai_targeting_scores.json")
print(f"\n{'='*65}")
print("MODEL SUMMARY")
print(f"{'='*65}")
print(f"  Algorithm   : RF ({acc:.1%}) + GBM + MLP Neural Net (Ensemble)")
print(f"  Accuracy    : {acc:.1%}")
print(f"  Precision   : {prec:.1%} (of predicted buyers, this many actually buy)")
print(f"  Recall      : {rec:.1%} (of all buyers, this many we find)")
print(f"  F1 Score    : {f1:.1%}")
print()
print(f"  TOP TARGET POOL (JAS 2025 buyers not yet in JAS 2026):")
print(f"    Total pool : 6,85,104 customers")
print(f"    Scored     : {len(df_pred):,} customers (top by historical spend)")
print(f"    Score > 80%: {int((df_pred['score']>80).sum()):,} (call TODAY)")
print(f"    Score > 60%: {int((df_pred['score']>60).sum()):,} (call this week)")
print()
print("TOP FEATURES DRIVING REPEAT PURCHASE:")
for feat, imp in fi_sorted[:8]:
    bar = '#' * int(imp * 30)
    print(f"  {feat:25} {imp*100:5.1f}%  {bar}")
