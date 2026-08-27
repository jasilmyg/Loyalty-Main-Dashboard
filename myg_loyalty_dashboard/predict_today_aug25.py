# -*- coding: utf-8 -*-
"""
===================================================================
myG Daily Sales Predictor - August 25, 2026
===================================================================
Predicts daily sales for August 25, 2026 using Deep Learning (BiLSTM)
and Machine Learning (Random Forest).

Features:
- English & Malayalam Calendar (Onam Season, Uthradam Eve)
- Weather Factor
- Output: Predicted Revenue, Transactions, Unique Customers
===================================================================
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ML Libraries
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import MinMaxScaler

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django
django.setup()

from analytics.clickhouse_service import get_ch_client

# ─── Try PyTorch ───────────────────────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader
    TORCH_AVAILABLE = True
    print("[OK] PyTorch available - Using BiLSTM Deep Learning")
except ImportError:
    TORCH_AVAILABLE = False
    print("[WARN] PyTorch not available - Deep Learning disabled")

# ═══════════════════════════════════════════════════════════════════════════════
# 1. LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════
def load_data():
    print("\n[DB] Loading daily sales from ClickHouse (azure_invoice_report)...")
    try:
        client = get_ch_client()
        query = """
            SELECT 
                toDate(date) AS date,
                COUNT(invoice_no) AS transactions,
                SUM(invoice_total) AS revenue,
                COUNT(DISTINCT customer_mobile) AS unique_customers
            FROM azure_invoice_report
            WHERE date IS NOT NULL
            GROUP BY date
            ORDER BY date
        """
        rows = client.query(query).result_rows
        
        df = pd.DataFrame(rows, columns=['date', 'transactions', 'revenue', 'unique_customers'])
        df['date'] = pd.to_datetime(df['date'])
        df['revenue'] = df['revenue'].astype(float)
        df['transactions'] = df['transactions'].astype(int)
        df['unique_customers'] = df['unique_customers'].astype(int)
        print(f"   [OK] Loaded {len(df)} days | Max Date: {df['date'].max().date()}")
        return df
    except Exception as e:
        print(f"   [ERROR] ClickHouse Error: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# 2. CALENDAR & WEATHER
# ═══════════════════════════════════════════════════════════════════════════════
def get_aug_weather():
    # Average August weather for Kerala (Monsoon trailing)
    return {
        'temperature': 27.5,
        'rainfall': 15.0,
        'humidity': 85.0,
        'weather_factor': 0.98,
        'reason': 'Moderate rain, standard monsoon trail'
    }

def get_event_factor(date_str):
    # Malayalam Calendar (Kollavarsham): Onam in 2026 is Aug 27 (Thiruvonam)
    # Aug 25 is Pooradam / Peak Onam shopping days
    events = {
        '2026-08-15': ('Independence Day (English)', 1.25),
        '2026-08-25': ('Peak Onam Shopping (Malayalam Calendar)', 1.45), # Huge multiplier for Onam
        '2026-08-26': ('Uthradam - Onam Eve (Malayalam)', 1.60),
        '2026-08-27': ('Thiruvonam (Malayalam)', 0.50), # Shops mostly closed
    }
    return events.get(date_str, ('Normal Day', 1.0))

def build_features(df):
    df = df.copy().sort_values('date').reset_index(drop=True)
    df['dayofweek'] = df['date'].dt.dayofweek
    df['month'] = df['date'].dt.month
    df['day'] = df['date'].dt.day
    df['is_weekend'] = (df['dayofweek'] >= 5).astype(int)
    
    # Lag features
    df['lag_1d'] = df['revenue'].shift(1)
    df['lag_7d'] = df['revenue'].shift(7)
    df['roll_7d'] = df['revenue'].rolling(7).mean()
    df['txn_lag_1d'] = df['transactions'].shift(1)
    
    df = df.dropna().reset_index(drop=True)
    return df

# ═══════════════════════════════════════════════════════════════════════════════
# 3. MODELS
# ═══════════════════════════════════════════════════════════════════════════════
def train_rf(df):
    features = ['dayofweek', 'month', 'day', 'is_weekend', 'lag_1d', 'lag_7d', 'roll_7d']
    X = df[features].values
    y_rev = df['revenue'].values
    y_txn = df['transactions'].values
    y_cus = df['unique_customers'].values
    
    rf_rev = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, y_rev)
    rf_txn = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, y_txn)
    rf_cus = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, y_cus)
    
    return rf_rev, rf_txn, rf_cus, features

if TORCH_AVAILABLE:
    class SalesBiLSTM(nn.Module):
        def __init__(self, input_dim):
            super().__init__()
            self.bilstm = nn.LSTM(input_dim, 64, num_layers=2, batch_first=True, bidirectional=True)
            self.fc = nn.Linear(128, 3) # rev, txn, cus
            
        def forward(self, x):
            out, _ = self.bilstm(x)
            return self.fc(out[:, -1, :])

def train_dl(df, features):
    if not TORCH_AVAILABLE: return None, None, None
    
    X = df[features].values.astype(np.float32)
    y = df[['revenue', 'transactions', 'unique_customers']].values.astype(np.float32)
    
    sc_X = MinMaxScaler()
    sc_y = MinMaxScaler()
    
    X_sc = sc_X.fit_transform(X)
    y_sc = sc_y.fit_transform(y)
    
    seq_len = 14
    X_seq, y_seq = [], []
    for i in range(len(X_sc) - seq_len):
        X_seq.append(X_sc[i:i+seq_len])
        y_seq.append(y_sc[i+seq_len])
        
    X_t = torch.tensor(np.array(X_seq))
    y_t = torch.tensor(np.array(y_seq))
    
    model = SalesBiLSTM(len(features))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    criterion = nn.MSELoss()
    
    model.train()
    for epoch in range(20):
        optimizer.zero_grad()
        pred = model(X_t)
        loss = criterion(pred, y_t)
        loss.backward()
        optimizer.step()
        
    model.eval()
    return model, sc_X, sc_y

# ═══════════════════════════════════════════════════════════════════════════════
# 4. PREDICT TODAY (Aug 25, 2026)
# ═══════════════════════════════════════════════════════════════════════════════
def predict_today(df, rf_models, dl_artifacts, features):
    target_date = pd.to_datetime('2026-08-25')
    
    # Estimate lags from latest available data
    latest = df.iloc[-1]
    last_7 = df.iloc[-7:]['revenue'].mean()
    
    feat_dict = {
        'dayofweek': target_date.dayofweek,
        'month': target_date.month,
        'day': target_date.day,
        'is_weekend': 1 if target_date.dayofweek >= 5 else 0,
        'lag_1d': latest['revenue'],
        'lag_7d': df.iloc[-7]['revenue'] if len(df) >= 7 else latest['revenue'],
        'roll_7d': last_7
    }
    
    X_input = np.array([[feat_dict[f] for f in features]])
    
    # ML Prediction
    rf_rev, rf_txn, rf_cus, _ = rf_models
    ml_rev = rf_rev.predict(X_input)[0]
    ml_txn = rf_txn.predict(X_input)[0]
    ml_cus = rf_cus.predict(X_input)[0]
    
    # DL Prediction
    dl_rev, dl_txn, dl_cus = 0, 0, 0
    if dl_artifacts[0] is not None:
        model, sc_X, sc_y = dl_artifacts
        
        recent_X = df[features].values[-13:]
        seq_input = np.vstack([recent_X, X_input])
        seq_sc = sc_X.transform(seq_input)
        
        with torch.no_grad():
            t_in = torch.tensor(seq_sc, dtype=torch.float32).unsqueeze(0)
            pred_sc = model(t_in).numpy()
            
        pred_raw = sc_y.inverse_transform(pred_sc)[0]
        dl_rev, dl_txn, dl_cus = pred_raw[0], pred_raw[1], pred_raw[2]
        
    # Apply Calendars & Weather
    weather = get_aug_weather()
    event_name, event_mult = get_event_factor('2026-08-25')
    
    combined_factor = weather['weather_factor'] * event_mult
    
    # Final ML
    final_ml_rev = ml_rev * combined_factor
    final_ml_txn = ml_txn * combined_factor
    final_ml_cus = ml_cus * combined_factor
    
    # Final DL
    final_dl_rev = dl_rev * combined_factor if dl_rev else 0
    final_dl_txn = dl_txn * combined_factor if dl_txn else 0
    final_dl_cus = dl_cus * combined_factor if dl_cus else 0
    
    print("\n" + "="*60)
    print(" PREDICTION: AUGUST 25, 2026 (TODAY) [CLICKHOUSE]")
    print("="*60)
    print(f" Calendars: ")
    print(f"  - English/Gregorian: August 25 (Tuesday)")
    print(f"  - Malayalam Event:   {event_name}")
    print(f"  - Event Multiplier:  x{event_mult:.2f} (Festival Spike)")
    print(f" Weather: {weather['temperature']}C, {weather['rainfall']}mm rain (Factor: x{weather['weather_factor']:.2f})")
    print(f" Combined Multiplier: x{combined_factor:.3f}")
    print("-"*60)
    print(" [1] MACHINE LEARNING (Random Forest) Forecast:")
    print(f"     Revenue:          Rs. {final_ml_rev:,.2f}")
    print(f"     Transactions:     {int(final_ml_txn):,}")
    print(f"     Customers:        {int(final_ml_cus):,}")
    print("-"*60)
    if TORCH_AVAILABLE:
        print(" [2] DEEP LEARNING (BiLSTM PyTorch) Forecast:")
        print(f"     Revenue:          Rs. {final_dl_rev:,.2f}")
        print(f"     Transactions:     {int(final_dl_txn):,}")
        print(f"     Customers:        {int(final_dl_cus):,}")
    else:
        print(" [2] DEEP LEARNING: Not available")
    print("="*60)
    print("Ensemble (Average) Recommendation:")
    ens_rev = (final_ml_rev + final_dl_rev)/2 if TORCH_AVAILABLE else final_ml_rev
    ens_txn = (final_ml_txn + final_dl_txn)/2 if TORCH_AVAILABLE else final_ml_txn
    ens_cus = (final_ml_cus + final_dl_cus)/2 if TORCH_AVAILABLE else final_ml_cus
    print(f"   --> Revenue: Rs. {ens_rev:,.0f} | Txns: {int(ens_txn)} | Custs: {int(ens_cus)}")
    
if __name__ == '__main__':
    df = load_data()
    if df is not None:
        df_feat = build_features(df)
        rf_models = train_rf(df_feat)
        dl_artifacts = train_dl(df_feat, rf_models[3])
        predict_today(df_feat, rf_models, dl_artifacts, rf_models[3])
