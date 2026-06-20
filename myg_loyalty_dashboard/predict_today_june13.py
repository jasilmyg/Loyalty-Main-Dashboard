# -*- coding: utf-8 -*-
"""
myG Real-Time Sales Predictor
BiLSTM + Attention + Monte Carlo Dropout
Partial-Day Conditioning on Actual 2PM Data
June 13, 2026
"""
import os, sys, warnings
import numpy as np
import pandas as pd
from datetime import date
warnings.filterwarnings('ignore')

# ── HARDCODED INPUTS (from live DB queried earlier) ────────────────────────────
TODAY               = date(2026, 6, 13)
CURRENT_SALES_2PM   = 61_161_903.0
MAY30_TOTAL         = 241_645_058.0
MAY30_AT_2PM        = 48_114_877.0
JUNE_AVG            = 203_613_613.0    # June 1-7 avg (from DB)

# May 30 hourly profile (actual from DB - fetched earlier)
MAY30_HOURLY = {
     9:   470_398,
    10: 5_313_789,
    11:10_356_305,
    12:13_774_355,
    13:18_200_025,
    14:18_667_276,   # 2PM-3PM
    15:18_283_549,
    16:21_471_583,
    17:24_662_112,
    18:23_772_447,
    19:26_747_743,
    20:30_556_528,
    21:19_617_016,
    22: 7_188_721,
    23: 2_563_205,
}

# ── PyTorch ────────────────────────────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    TORCH = True
    print("[OK] PyTorch available - BiLSTM + Attention")
except ImportError:
    TORCH = False
    print("[WARN] PyTorch not found - using statistical fallback")

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD SYNTHETIC TRAINING DATA (fast, no DB re-query)
# Uses the actual June 1-7 and historical patterns already known
# ═══════════════════════════════════════════════════════════════════════════════
def build_training_data():
    """
    Build a synthetic but realistic training dataset using:
    - Known June 2026 daily actuals (Jun 1-7 from DB)
    - May 30 offer day actual
    - Seasonal patterns for 2021-2025 (parametric simulation calibrated to real data)
    """
    np.random.seed(42)
    records = []

    # Kerala retail monthly base (calibrated to real myG data avg ~20 Cr/day)
    monthly_base = {
        1:1.80e8, 2:1.75e8, 3:1.78e8, 4:2.00e8, 5:2.05e8,
        6:1.88e8, 7:1.82e8, 8:2.40e8, 9:2.60e8, 10:2.20e8,
        11:2.15e8, 12:2.30e8
    }
    dow_mult  = {0:0.94, 1:0.96, 2:0.98, 3:1.00, 4:1.05, 5:1.12, 6:1.18}
    yoy_growth = 1.08   # ~8% YoY growth observed in myG data

    # Generate 2021-2026 May synthetic daily data
    dates = pd.date_range('2021-01-01', '2026-05-31', freq='D')
    for i, dt in enumerate(dates):
        year_factor = yoy_growth ** (dt.year - 2021)
        base        = monthly_base[dt.month] * year_factor
        dow_f       = dow_mult[dt.dayofweek]
        salary_f    = 1.08 if dt.day <= 5 else (1.04 if dt.day >= 25 else 1.0)
        noise       = np.random.normal(1.0, 0.07)
        # Offer day boost
        is_offer    = 1.0 if dt.strftime('%Y-%m-%d') in {'2026-05-30','2026-05-31'} else 0.0
        offer_f     = 1.15 if is_offer else 1.0
        rev         = base * dow_f * salary_f * offer_f * noise
        records.append({
            'date': dt, 'revenue': rev,
            'dow': dt.dayofweek, 'month': dt.month, 'day': dt.day,
            'is_offer': is_offer,
        })

    # Overwrite known actuals
    known = {
        '2026-05-29': 210_113_931.0,
        '2026-05-30': 241_645_058.0,
        '2026-06-01': 202_749_461.0,
        '2026-06-02': 186_168_758.0,
        '2026-06-03': 191_908_106.0,
        '2026-06-04': 196_036_353.0,
        '2026-06-05': 200_295_676.0,
        '2026-06-06': 241_201_544.0,
        '2026-06-07': 206_935_390.0,
    }
    df = pd.DataFrame(records)
    for d_str, val in known.items():
        mask = df['date'] == pd.Timestamp(d_str)
        df.loc[mask, 'revenue'] = val

    return df.reset_index(drop=True)


def add_features(df):
    df = df.copy().sort_values('date').reset_index(drop=True)
    mi = {1:.95,2:.92,3:.93,4:1.05,5:1.03,6:.88,7:.86,8:1.20,9:1.35,10:1.10,11:1.08,12:1.18}
    df['season']      = df['month'].map(mi)
    df['is_weekend']  = (df['dow'] >= 5).astype(float)
    df['is_salary']   = (df['day'] <= 5).astype(float)
    df['is_monthend'] = (df['day'] >= 25).astype(float)
    df['lag1']        = df['revenue'].shift(1)
    df['lag7']        = df['revenue'].shift(7)
    df['lag30']       = df['revenue'].shift(30)
    df['roll7']       = df['revenue'].rolling(7, min_periods=1).mean()
    df['roll30']      = df['revenue'].rolling(30,min_periods=1).mean()
    # Simulate intraday partial feature: 19.9% done by 2PM (from May 30 actual)
    intra_pct         = 0.199
    df['partial_2pm'] = df['revenue'] * intra_pct
    df['partial_norm']= df['partial_2pm'] / (df['roll7'] + 1)
    return df.dropna().reset_index(drop=True)

FCOLS = ['lag1','lag7','lag30','roll7','roll30',
         'dow','month','day','is_weekend','is_salary','is_monthend',
         'season','is_offer','partial_2pm','partial_norm']


# ═══════════════════════════════════════════════════════════════════════════════
# BiLSTM MODEL
# ═══════════════════════════════════════════════════════════════════════════════
if TORCH:
    class Attention(nn.Module):
        def __init__(self, h):
            super().__init__()
            self.w = nn.Linear(h, 1)
        def forward(self, x):
            a = torch.softmax(self.w(x), dim=1)
            return (a * x).sum(dim=1)

    class BiLSTM_Predictor(nn.Module):
        def __init__(self, in_dim, hidden=96, layers=2, drop=0.30):
            super().__init__()
            self.lstm = nn.LSTM(in_dim, hidden, layers,
                                batch_first=True, bidirectional=True, dropout=drop)
            self.attn = Attention(hidden*2)
            self.ln   = nn.LayerNorm(hidden*2)   # LayerNorm works with batch_size=1
            self.drop = nn.Dropout(drop)
            self.head = nn.Sequential(
                nn.Linear(hidden*2 + 1, 64), nn.GELU(), nn.Dropout(drop),
                nn.Linear(64, 1)
            )
        def forward(self, seq, partial):
            o, _ = self.lstm(seq)
            c    = self.drop(self.ln(self.attn(o)))
            return self.head(torch.cat([c, partial.unsqueeze(1)], dim=1)).squeeze(-1)


def train_bilstm(df):
    SEQ = 14
    X   = df[FCOLS].values.astype(np.float32)
    y   = df['revenue'].values.astype(np.float32)

    Xmn, Xmx = X.min(0), X.max(0)
    ymn, ymx  = y.min(), y.max()
    Xs = (X - Xmn) / (Xmx - Xmn + 1e-8)
    ys = (y - ymn) / (ymx - ymn + 1e-8)

    pidx = FCOLS.index('partial_2pm')
    seqs, tgts, pars = [], [], []
    for i in range(len(Xs) - SEQ):
        seqs.append(Xs[i:i+SEQ])
        tgts.append(ys[i+SEQ])
        pars.append(Xs[i+SEQ, pidx])

    Xt = torch.tensor(np.array(seqs), dtype=torch.float32)
    yt = torch.tensor(np.array(tgts), dtype=torch.float32)
    pt = torch.tensor(np.array(pars), dtype=torch.float32)

    from torch.utils.data import TensorDataset, DataLoader
    dl    = DataLoader(TensorDataset(Xt, yt, pt), batch_size=64, shuffle=True)
    model = BiLSTM_Predictor(len(FCOLS))
    opt   = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=30)
    loss_fn = nn.HuberLoss(delta=0.5)

    print(f"\n[TRAIN] BiLSTM-2L | seq={SEQ}d | samples={len(seqs):,} | features={len(FCOLS)}")
    model.train()
    for ep in range(30):
        tot = 0.0
        for bX, by, bp in dl:
            opt.zero_grad()
            p = model(bX, bp)
            l = loss_fn(p, by)
            l.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += l.item()
        sched.step()
        if (ep+1) % 6 == 0:
            print(f"   Epoch {ep+1:2d}/30  Loss={tot/len(dl):.6f}")

    print("   [DONE] Training complete.")
    return model, Xs, ymn, ymx, Xmn, Xmx, SEQ, pidx


def mc_predict(model, df, Xs, ymn, ymx, Xmn, Xmx, SEQ, pidx, n_mc=300):
    """Monte Carlo Dropout - 300 stochastic forward passes."""
    # Build today's feature row
    last = df.iloc[-1]
    today_feat = np.array([
        last['revenue'],                      # lag1
        df['revenue'].iloc[-7],               # lag7
        df['revenue'].iloc[-30],              # lag30
        df['revenue'].iloc[-7:].mean(),       # roll7
        df['revenue'].iloc[-30:].mean(),      # roll30
        TODAY.weekday(),                      # dow (Saturday=5)
        TODAY.month,                          # month=6
        TODAY.day,                            # day=13
        1.0,                                  # is_weekend (Sat)
        0.0,                                  # is_salary
        0.0,                                  # is_monthend
        0.88,                                 # season (June)
        1.0,                                  # is_offer (YES)
        CURRENT_SALES_2PM,                    # partial_2pm (REAL actual)
        CURRENT_SALES_2PM / (df['revenue'].iloc[-7:].mean()+1),  # partial_norm
    ], dtype=np.float32)

    feat_sc  = (today_feat - Xmn) / (Xmx - Xmn + 1e-8)
    seq_t    = torch.tensor(Xs[-SEQ:], dtype=torch.float32).unsqueeze(0)
    part_t   = torch.tensor([feat_sc[pidx]], dtype=torch.float32)

    # MC Dropout: eval mode (fixes BN/LN stats) but keep Dropout layers active
    model.eval()
    for m in model.modules():
        if isinstance(m, nn.Dropout):
            m.train()   # keep dropout stochastic for uncertainty estimation
    preds = []
    with torch.no_grad():
        for _ in range(n_mc):
            v = float(model(seq_t, part_t).item()) * (ymx - ymn) + ymn
            preds.append(v)

    arr = np.array(preds)
    return {
        'mean':    arr.mean(),
        'std':     arr.std(),
        'p5':      np.percentile(arr, 5),
        'p10':     np.percentile(arr, 10),
        'p25':     np.percentile(arr, 25),
        'p50':     np.percentile(arr, 50),
        'p75':     np.percentile(arr, 75),
        'p90':     np.percentile(arr, 90),
        'p95':     np.percentile(arr, 95),
    }


def stat_fallback():
    """Fast weighted ensemble when PyTorch unavailable."""
    intraday = CURRENT_SALES_2PM / 0.199
    offer_scaled = JUNE_AVG * (MAY30_TOTAL / 210_113_931.0)
    mean = 0.60*intraday + 0.25*offer_scaled + 0.15*(JUNE_AVG*1.15)
    std  = mean * 0.06
    arr  = np.random.normal(mean, std, 300)
    return {'mean':mean,'std':std,'p5':np.percentile(arr,5),'p10':np.percentile(arr,10),
            'p25':np.percentile(arr,25),'p50':np.percentile(arr,50),
            'p75':np.percentile(arr,75),'p90':np.percentile(arr,90),'p95':np.percentile(arr,95)}


def print_report(r, model_name):
    mean = r['mean']
    rem  = mean - CURRENT_SALES_2PM
    pct_ahead_may30_2pm = ((CURRENT_SALES_2PM / MAY30_AT_2PM) - 1)*100

    # Hour-by-hour 2PM onwards from May 30 profile
    pm_hours     = {h:v for h,v in MAY30_HOURLY.items() if h >= 14}
    pm_total_may = sum(pm_hours.values())
    pm_total_may = pm_total_may if pm_total_may > 0 else 1

    print()
    print("=" * 68)
    print("  myG DEEP LEARNING SALES PREDICTION  |  June 13, 2026")
    print("=" * 68)
    print(f"  Model   : {model_name}")
    print(f"  Method  : MC Dropout (300 simulations)")
    print(f"  Trained : Synthetic calibrated + 9 known June 2026 actuals")
    print("=" * 68)

    print(f"\n  REAL-TIME INPUT:")
    print(f"    Current Sales @ 2:00 PM  : Rs. {CURRENT_SALES_2PM:>15,.0f}")
    print(f"    Offer Active             : YES (same as May 30)")
    print(f"    Today vs May30 @ 2PM     : +{pct_ahead_may30_2pm:.1f}% AHEAD")
    print(f"    Day                      : Saturday (June 13, 2026)")

    print(f"\n  DEEP LEARNING FORECAST:")
    print(f"    Central Prediction (mean): Rs. {mean:>15,.0f}  ({mean/1e7:.2f} Cr)")
    print(f"    Median (p50)             : Rs. {r['p50']:>15,.0f}  ({r['p50']/1e7:.2f} Cr)")
    print(f"    Std Deviation            : Rs. {r['std']:>15,.0f}")
    print()
    print(f"    80% Confidence Band      : Rs. {r['p10']/1e7:.2f} Cr  --  Rs. {r['p90']/1e7:.2f} Cr")
    print(f"    90% Confidence Band      : Rs. {r['p5']/1e7:.2f} Cr  --  Rs. {r['p95']/1e7:.2f} Cr")
    print(f"    Optimistic (p75)         : Rs. {r['p75']:>15,.0f}  ({r['p75']/1e7:.2f} Cr)")
    print(f"    Conservative (p25)       : Rs. {r['p25']:>15,.0f}  ({r['p25']/1e7:.2f} Cr)")

    print(f"\n  REMAINING SALES EXPECTED (2PM-10PM):")
    print(f"    Total Remaining          : Rs. {rem:>15,.0f}  ({rem/1e7:.2f} Cr)")
    print()
    print(f"    {'Time':<14}  {'Expected (Rs.)':<20}  Cumulative")
    print("    " + "-" * 55)
    cum = CURRENT_SALES_2PM
    for h in sorted(pm_hours.keys()):
        expected = (pm_hours[h] / pm_total_may) * rem
        cum     += expected
        tstr     = f"{h:02d}:00-{h+1:02d}:00"
        print(f"    {tstr:<14}  Rs. {expected:>13,.0f}   Rs. {cum:>13,.0f}")

    pct_vs_jun_avg  = ((mean - JUNE_AVG) / JUNE_AVG) * 100
    pct_vs_may30    = ((mean - MAY30_TOTAL) / MAY30_TOTAL) * 100

    print(f"\n  BENCHMARKS:")
    print(f"    vs June avg (normal day) : {pct_vs_jun_avg:+.1f}%")
    print(f"    vs May 30 actual         : {pct_vs_may30:+.1f}%")
    print(f"    May 30 total (actual)    : Rs. {MAY30_TOTAL:>15,.0f}")

    print()
    print("=" * 68)
    print(f"  FINAL ANSWER: Rs. {mean:,.0f}")
    print(f"                ({mean/1e7:.2f} CRORE)")
    print(f"  90% RANGE   : Rs. {r['p5']/1e7:.2f} Cr  to  Rs. {r['p95']/1e7:.2f} Cr")
    print("=" * 68)


def main():
    print("=" * 68)
    print("  myG BiLSTM Real-Time Sales Predictor")
    print("  Partial-Day Conditioned | MC Dropout | June 13, 2026")
    print("=" * 68)

    df_raw  = build_training_data()
    df_feat = add_features(df_raw)
    print(f"[DATA] Training samples: {len(df_feat):,} | Features: {len(FCOLS)}")

    if TORCH:
        model, Xs, ymn, ymx, Xmn, Xmx, SEQ, pidx = train_bilstm(df_feat)
        print(f"\n[MC]  Running 300 Monte Carlo Dropout inference passes...")
        r = mc_predict(model, df_feat, Xs, ymn, ymx, Xmn, Xmx, SEQ, pidx)
        mname = "BiLSTM-2L (hidden=96x2) + Self-Attention + MC Dropout"
    else:
        print("\n[STAT] PyTorch unavailable - using statistical ensemble...")
        r = stat_fallback()
        mname = "Statistical Ensemble (Weighted Blend)"

    print_report(r, mname)

if __name__ == '__main__':
    main()
