#!/usr/bin/env python3
"""
For each symbol, print how many 1-min bars pass each of our filters:
  – total bars
  – RV ≥ threshold
  – %↑ vs open ≥ threshold
  – momentum > 0   (ema9 − ema20)
  – MACD > signal
  – price > VWAP
  – final signals
"""

import os
import pandas as pd
from glob import glob

# ── CONFIG ────────────────────────────────────────────────────────────────────
PROCESSED_DIR       = os.path.join('data', 'processed')
RV_WINDOW           = 60
RV_THRESHOLD        = 5.0
PRICE_CHANGE_THRESH = 0.10
# ───────────────────────────────────────────────────────────────────────────────

def load_and_indicate(symbol):
    path = os.path.join(PROCESSED_DIR, f"{symbol}_processed.csv")
    df = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')

    # 1) rolling volume ratio (RV)
    df['avg_vol60'] = df['volume'].rolling(RV_WINDOW, min_periods=1).mean()
    df['rv']        = df['volume'] / df['avg_vol60']

    # 2) % off open
    open0 = df['close'].iloc[0]
    df['pct_open']  = (df['close'] - open0) / open0

    # 3) EMAs & momentum
    df['ema9']   = df['close'].ewm(span=9,  adjust=False).mean()
    df['ema20']  = df['close'].ewm(span=20, adjust=False).mean()
    df['momentum'] = df['ema9'] - df['ema20']

    # 4) VWAP
    # group by date so we reset VWAP each session
    df['date']   = df.index.date
    df['pv']     = (df['close'] * df['volume']).groupby(df['date']).cumsum()
    df['cv']     = df['volume'].groupby(df['date']).cumsum()
    df['vwap']   = df['pv'] / df['cv']

    # 5) MACD & signal
    df['ema12']     = df['close'].ewm(span=12, adjust=False).mean()
    df['ema26']     = df['close'].ewm(span=26, adjust=False).mean()
    df['macd']      = df['ema12'] - df['ema26']
    df['macd_sig']  = df['macd'].ewm(span=9, adjust=False).mean()

    return df

def analyze_symbol(symbol, df):
    total    = len(df)
    rv_ok    = (df['rv'] >= RV_THRESHOLD).sum()
    op_ok    = (df['pct_open'] >= PRICE_CHANGE_THRESH).sum()
    mom_ok   = (df['momentum'] > 0).sum()
    mac_ok   = (df['macd'] > df['macd_sig']).sum()
    vwap_ok  = (df['close'] > df['vwap']).sum()

    # final signals (long OR short)
    long_mask  = (
            (df['rv'] >= RV_THRESHOLD) &
            (df['pct_open'] >= PRICE_CHANGE_THRESH) &
            (df['momentum'] > 0) &
            (df['macd'] > df['macd_sig']) &
            (df['close'] > df['vwap'])
    )
    short_mask = (
            (df['momentum'] < 0) &
            (df['macd'] < df['macd_sig']) &
            (df['close'] < df['vwap'])
    )
    final_ok = (long_mask | short_mask).sum()

    print(
        f"{symbol:6} ┆ total {total:4d} ┆ rv {rv_ok:4d} ┆ %↑ {op_ok:4d} "
        f"┆ mom {mom_ok:4d} ┆ mac {mac_ok:4d} ┆ vwap {vwap_ok:4d} ┆ signals {final_ok:4d}"
    )

def main():
    csvs = glob(os.path.join(PROCESSED_DIR, "*_processed.csv"))
    syms = [os.path.basename(p).split("_")[0] for p in csvs]

    print("Analyzing filter pass-rates:\n")
    for sym in syms:
        df = load_and_indicate(sym)
        analyze_symbol(sym, df)

if __name__ == "__main__":
    main()
