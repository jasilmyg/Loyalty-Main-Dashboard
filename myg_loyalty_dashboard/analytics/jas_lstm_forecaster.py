import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    print("Warning: torch not installed.")

# Re-use the existing AI modules
from analytics.advanced_lstm_forecaster import (
    CustomerBiLSTMAttention,
    build_kerala_festival_calendar,
    simulate_kerala_weather,
    compute_festival_features,
    festival_multiplier_for_date,
    weather_multiplier_for_date
)

def run_jas_bilstm_forecast(jas_daily_actuals, remaining_days):
    """
    Trains BiLSTM on historical (simulated) data + JAS actuals, then predicts the remaining days.
    jas_daily_actuals: list of dicts [{'date': '2026-07-01', 'daily_new': 1200}, ...]
    remaining_days: int
    Returns: sum of projected daily repeats for the remaining days.
    """
    if not TORCH_AVAILABLE:
        print("Falling back to statistical average since PyTorch is unavailable.")
        if jas_daily_actuals:
            recent_avg = np.mean([x['daily_new'] for x in jas_daily_actuals[-7:]])
            return int(recent_avg * remaining_days)
        return 0

    JAS_START = pd.to_datetime("2026-07-01")
    if jas_daily_actuals:
        ACTUALS_END = pd.to_datetime(jas_daily_actuals[-1]['date'])
    else:
        ACTUALS_END = JAS_START - timedelta(days=1)
        
    FY_END = pd.to_datetime("2026-09-30")

    # 1. Build Calendars and Weather
    festival_calendar = build_kerala_festival_calendar()
    full_date_range = pd.date_range(start="2020-07-01", end=FY_END, freq='D')
    df_weather = simulate_kerala_weather(full_date_range)
    df_weather['Date'] = full_date_range

    # 2. Simulate historical data up to JAS start
    hist_start  = pd.to_datetime("2020-07-01")
    hist_dates   = pd.date_range(start=hist_start, end=JAS_START - timedelta(days=1), freq='D')
    baseline    = np.linspace(500, 3500, len(hist_dates))

    df_hist = pd.DataFrame({'Date': hist_dates, 'baseline': baseline})
    df_hist = df_hist.merge(df_weather, on='Date', how='left')
    
    df_hist['month']      = df_hist['Date'].dt.month
    df_hist['dayofweek']  = df_hist['Date'].dt.dayofweek

    weekly_seasonality = np.where(df_hist['dayofweek'] >= 5, 1.20, 1.0)

    df_hist['festival_multiplier'] = df_hist['Date'].apply(
        lambda d: festival_multiplier_for_date(d, festival_calendar)
    )
    df_hist['weather_multiplier'] = df_hist.apply(
        lambda row: weather_multiplier_for_date(row['temperature'], row['rainfall'], row['humidity']),
        axis=1
    )

    np.random.seed(42)
    noise = np.random.normal(0, 100, len(df_hist))
    df_hist['daily_repeat'] = (
        df_hist['baseline'] * df_hist['festival_multiplier'] * df_hist['weather_multiplier'] * weekly_seasonality
    ) + noise
    df_hist['daily_repeat'] = df_hist['daily_repeat'].clip(lower=100)

    # 3. Append JAS actuals
    jas_rows = []
    for d in jas_daily_actuals:
        # Expected dict: {'date': '2026-07-01', 'cum': 500}
        # But we need daily_new. Wait, we should compute daily_new from cum if not provided.
        jas_rows.append({'Date': pd.to_datetime(d['date']), 'daily_repeat': d.get('daily_new', 0)})
        
    if jas_rows:
        df_jas = pd.DataFrame(jas_rows)
        # Calculate daily new from cumulative if necessary
        if df_jas['daily_repeat'].sum() == 0:
            cums = [d['cum'] for d in jas_daily_actuals]
            daily_new = [cums[0]] + [cums[i] - cums[i-1] for i in range(1, len(cums))]
            df_jas['daily_repeat'] = daily_new

        df_jas = df_jas.merge(df_weather, on='Date', how='left')
        df_hist = pd.concat([df_hist[['Date', 'daily_repeat', 'temperature', 'rainfall', 'humidity']], 
                             df_jas[['Date', 'daily_repeat', 'temperature', 'rainfall', 'humidity']]], ignore_index=True)

    # 4. Compute Advanced Features
    fest_features = compute_festival_features(df_hist['Date'], festival_calendar)
    df_hist = pd.concat([df_hist.reset_index(drop=True), fest_features.reset_index(drop=True)], axis=1)

    df_hist['lag_1']      = df_hist['daily_repeat'].shift(1)
    df_hist['lag_7']      = df_hist['daily_repeat'].shift(7)
    df_hist['lag_30']     = df_hist['daily_repeat'].shift(30)
    df_hist['rolling_7']  = df_hist['daily_repeat'].rolling(window=7).mean()
    df_hist = df_hist.dropna().reset_index(drop=True)

    feature_cols = [
        'lag_1', 'lag_7', 'lag_30', 'rolling_7',
        'is_festival', 'days_before_festival', 'days_after_festival',
        'festival_weight', 'is_salary_period',
        'temperature', 'rainfall', 'humidity'
    ]

    # 5. Model Setup & Training
    X_raw = df_hist[feature_cols].values
    y_raw = df_hist['daily_repeat'].values.reshape(-1, 1)

    X_min, X_max = X_raw.min(axis=0), X_raw.max(axis=0)
    y_min, y_max = y_raw.min(axis=0), y_raw.max(axis=0)
    
    # Avoid div by zero
    X_max_adj = np.where(X_max == X_min, X_max + 1, X_max)
    y_max_adj = np.where(y_max == y_min, y_max + 1, y_max)
    
    X_scaled = (X_raw - X_min) / (X_max_adj - X_min + 1e-8)
    y_scaled = (y_raw - y_min) / (y_max_adj - y_min + 1e-8)

    seq_length = 14
    X_seq, y_seq = [], []
    for i in range(len(X_scaled) - seq_length):
        X_seq.append(X_scaled[i:i + seq_length])
        y_seq.append(y_scaled[i + seq_length])

    if len(X_seq) == 0:
        return 0

    X_tensor = torch.tensor(np.array(X_seq), dtype=torch.float32)
    y_tensor = torch.tensor(np.array(y_seq), dtype=torch.float32)

    dataset    = TensorDataset(X_tensor, y_tensor)
    dataloader = DataLoader(dataset, batch_size=32, shuffle=True)

    model     = CustomerBiLSTMAttention(input_size=len(feature_cols), hidden_size=64, num_layers=2, output_size=1)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005)

    model.train()
    epochs = 15
    print(f"Training BiLSTM over {epochs} epochs...")
    for epoch in range(epochs):
        epoch_loss = 0.0
        for batch_X, batch_y in dataloader:
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
        
    print("Training complete. Forecasting remaining JAS days...")

    # 6. Auto-regressive Forecast
    model.eval()
    forecast_dates  = pd.date_range(start=ACTUALS_END + timedelta(days=1), end=FY_END, freq='D')
    forecast_daily  = []
    forecast_pts    = []
    
    current_seq  = X_tensor[-1].unsqueeze(0).clone()
    hist_buffer  = list(df_hist['daily_repeat'].values[-30:])

    # Start cumulative projection from the last actual cumulative value
    current_cum = 0
    if jas_daily_actuals:
        current_cum = jas_daily_actuals[-1].get('cum', 0)

    with torch.no_grad():
        for d in forecast_dates:
            pred_scaled = model(current_seq)
            pred = pred_scaled.item() * float(y_max_adj[0] - y_min[0]) + float(y_min[0])
            pred = max(2000, min(6500, pred))

            fm = festival_multiplier_for_date(d, festival_calendar)
            pred *= fm
            if d.day <= 5:
                pred *= 1.07

            weather_row = df_weather[df_weather['Date'] == d].iloc[0]
            temp_d = weather_row['temperature']
            rain_d = weather_row['rainfall']
            hum_d = weather_row['humidity']
            wm = weather_multiplier_for_date(temp_d, rain_d, hum_d)
            pred *= wm

            forecast_daily.append(pred)
            hist_buffer.append(pred)
            
            current_cum += int(pred)
            forecast_pts.append({
                "date": d.strftime('%Y-%m-%d'),
                "cum": current_cum
            })

            date_str = d.strftime('%Y-%m-%d')
            if date_str in festival_calendar:
                is_fest_next   = 1
                fest_w_next    = festival_calendar[date_str][1]
            else:
                is_fest_next   = 0
                fest_w_next    = fm

            future_fests = [pd.to_datetime(k) for k in festival_calendar if pd.to_datetime(k) > d]
            past_fests   = [pd.to_datetime(k) for k in festival_calendar if pd.to_datetime(k) <= d]
            db_next = min(15, (min(future_fests) - d).days) if future_fests else 15
            da_next = min(15, (d - max(past_fests)).days)   if past_fests   else 15
            salary_next = 1 if d.day <= 5 else 0

            new_feat = np.array([
                hist_buffer[-2],
                hist_buffer[-8],
                hist_buffer[-31] if len(hist_buffer) >= 31 else hist_buffer[0],
                np.mean(hist_buffer[-7:]),
                is_fest_next,
                db_next,
                da_next,
                fest_w_next,
                salary_next,
                temp_d,
                rain_d,
                hum_d
            ])
            new_feat_scaled = (new_feat - X_min) / (X_max_adj - X_min + 1e-8)

            current_seq = torch.cat((
                current_seq[:, 1:, :],
                torch.tensor(new_feat_scaled, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
            ), dim=1)

    return int(sum(forecast_daily)), forecast_pts
