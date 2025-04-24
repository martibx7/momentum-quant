#!/usr/bin/env python3
"""
exit_signals.py

Generate exit timestamps for each entry in signals_<date>.csv based on:
 1) Initial stop-loss (from generate_signals)
 2) Trailing stop based on regime
 3) Momentum die-out: EMA9 ≤ EMA20
 4) MACD cross: MACD ≤ MACD_signal
 5) VWAP cross: close ≤ VWAP
 6) Time-stop: exit at or after 15:45

Now also carries through `shares` from entry signals for P&L sizing.

Usage:
  python exit_signals.py --date YYYY-MM-DD

Outputs:
  data/signals/exits_<date>.csv with:
    ticker, entry_ts, exit_ts, exit_reason, entry_price, exit_price, regime, shares
"""
import os
import argparse
import pandas as pd
from datetime import time

# ——— CONFIG ———
PROCESSED_DIR = os.path.join('data', 'processed')
SIGNALS_DIR   = os.path.join('data', 'signals')
TIME_STOP     = time(15, 45)
# Trailing-stop percentages by regime
TRAIL_PCT = {
    'strong_bull': 0.05,  # 5%
    'neutral':     0.03,  # 3%
    'bearish':     0.015  # 1.5%
}


def parse_args():
    p = argparse.ArgumentParser(description='Generate exit signals by date')
    p.add_argument('--date', required=True, help='Date in YYYY-MM-DD')
    return p.parse_args()


def load_entries(date_str):
    path = os.path.join(SIGNALS_DIR, f'signals_{date_str}.csv')
    return pd.read_csv(path, parse_dates=['timestamp'])


def find_exit(sym, date_str, entry_ts, stop_price, regime):
    path = os.path.join(PROCESSED_DIR, f'{sym}_{date_str}.csv')
    df = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
    df2 = df[df.index > entry_ts]

    # no data → exit at entry
    if df2.empty:
        price = df.loc[entry_ts]['close'] if entry_ts in df.index else df['close'].iloc[-1]
        return entry_ts, 'no_data', price

    # 1) initial stop
    hits = df2[df2['low'] <= stop_price]
    if not hits.empty:
        t = hits.index[0]
        return t, 'stop_loss', df2.loc[t]['open']

    # 2) trailing stop
    trail = TRAIL_PCT.get(regime, TRAIL_PCT['neutral'])
    high_water = df.loc[entry_ts]['close']
    for t, row in df2.iterrows():
        high_water = max(high_water, row['high'])
        if row['close'] <= high_water * (1 - trail):
            return t, 'trail_stop', row['open']

    # 3) momentum die-out
    mom = df2[df2['ema9'] <= df2['ema20']]
    if not mom.empty:
        t = mom.index[0]
        return t, 'momentum_end', df2.loc[t]['open']

    # 4) MACD cross
    mac = df2[df2['macd'] <= df2['macd_signal']]
    if not mac.empty:
        t = mac.index[0]
        return t, 'macd_cross', df2.loc[t]['open']

    # 5) VWAP cross
    vw = df2[df2['close'] <= df2['vwap']]
    if not vw.empty:
        t = vw.index[0]
        return t, 'vwap_cross', df2.loc[t]['open']

    # 6) time-stop
    ts = df2.index[df2.index.time >= TIME_STOP]
    if len(ts):
        t = ts[0]
        return t, 'time_stop', df2.loc[t]['open']

    # fallback: end-of-day
    t = df2.index[-1]
    return t, 'end_of_day', df2.loc[t]['open']


def main():
    args = parse_args()
    date_str = args.date
    entries = load_entries(date_str)
    results = []

    for _, r in entries.iterrows():
        sym        = r['ticker']
        entry_ts   = pd.to_datetime(r['timestamp'])
        entry_pr   = r['entry_price']
        stop_pr    = r['stop_price']
        regime     = r.get('regime', 'neutral')
        shares     = int(r.get('shares', 1))

        exit_ts, reason, exit_pr = find_exit(sym, date_str, entry_ts, stop_pr, regime)
        results.append({
            'ticker':      sym,
            'entry_ts':    entry_ts,
            'entry_price': entry_pr,
            'exit_ts':     exit_ts,
            'exit_price':  exit_pr,
            'exit_reason': reason,
            'regime':      regime,
            'shares':      shares,
        })

    df_out = pd.DataFrame(results)
    out_path = os.path.join(SIGNALS_DIR, f'exits_{date_str}.csv')
    df_out.to_csv(out_path, index=False)
    print(f"✅ Wrote exit signals → {out_path}")


if __name__ == '__main__':
    main()
