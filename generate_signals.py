#!/usr/bin/env python3
"""
generate_signals.py  (VOLUME-BAR FILTER)

Scan processed 1-min bars for symbols against dynamic ETF barometer regime,
with bankroll management and volume-bar confirmation:
  • Breakout bar must be green and above avg intraday volume.
  • Pullback bar must be red and on lower volume than the breakout.
  • Entry bar must be green and on higher volume than the pullback.

Usage:
  python generate_signals.py --date YYYY-MM-DD [--bankroll NNNN]
"""
import os
import argparse
import pandas as pd
from glob import glob
import math

# ==== CONFIG ====
PROCESSED_DIR   = os.path.join('data', 'processed')
SIGNALS_DIR     = os.path.join('data', 'signals')
BASE_PCT_MOVE   = 0.10   # 10% baseline for breakout
BASE_PULLBACK   = 0.005  # 0.5% baseline for pullback
BAR_ETFS        = [('SPY',1), ('QQQ',1), ('IWM',2)]
DEFAULT_BANKROLL = 3000.0
RISK_PCT = {
    'strong_bull': 0.02,
    'neutral':      0.01,
    'bearish':      0.005
}

def parse_args():
    p = argparse.ArgumentParser(description='Generate entry signals by date with volume-bar filters')
    p.add_argument('--date',     required=True, help='YYYY-MM-DD')
    p.add_argument('--bankroll', type=float, default=DEFAULT_BANKROLL,
                   help=f'Starting capital (default {DEFAULT_BANKROLL})')
    return p.parse_args()

def load_processed(sym, date_str):
    path = os.path.join(PROCESSED_DIR, f"{sym}_{date_str}.csv")
    df = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
    # metadata cols check
    for c in ('prev_close','avg_daily_vol'):
        if c not in df.columns:
            raise KeyError(f"{sym} missing {c}")
    # clean and indicators
    df = df.dropna(subset=['high','low','open','close','volume'])
    df['avg_vol_intraday'] = df['volume'].rolling(30, min_periods=1).mean()
    df['cum_vol']  = df['volume'].cumsum()
    df['cum_high'] = df['high'].cummax()
    return df

def load_barometers(date_str):
    etfs = {}
    for sym, weight in BAR_ETFS:
        etfs[sym] = {'df': load_processed(sym, date_str),
                     'avg_daily_vol': load_processed(sym, date_str)['avg_daily_vol'].iloc[0]}
    return etfs

def get_regime_at(t0, etfs):
    score = 0
    for sym, weight in BAR_ETFS:
        df = etfs[sym]['df']
        slice_ = df[df.index <= t0]
        if slice_.empty: continue
        # price move
        if slice_['close'].iloc[-1] > slice_['close'].iloc[0]:
            score += weight
        # volume reached
        if slice_['cum_vol'].iloc[-1] >= etfs[sym]['avg_daily_vol']:
            score += weight
    if score >= 5:
        return 'strong_bull', BASE_PCT_MOVE*0.8, BASE_PULLBACK*0.6
    if score >= 3:
        return 'neutral', BASE_PCT_MOVE, BASE_PULLBACK
    return 'bearish', BASE_PCT_MOVE*1.2, BASE_PULLBACK*1.4

def generate_signals(date_str, bankroll):
    etfs = load_barometers(date_str)
    paths = glob(os.path.join(PROCESSED_DIR, f'*_{date_str}.csv'))
    symbols = sorted({os.path.basename(p).split('_')[0] for p in paths
                      if not p.startswith(tuple(s for s,_ in BAR_ETFS))})

    signals = []
    print(f"🔍 Generating signals for {len(symbols)} symbols on {date_str}...")
    for sym in symbols:
        df = load_processed(sym, date_str)
        prev_close = df['prev_close'].iloc[0]
        if prev_close <= 0 or pd.isna(prev_close):
            continue

        used_breaks = set()
        for t0, bar in df[df['high']==df['cum_high']].iterrows():
            # breakout must be green & above intraday avg
            if bar['close'] <= bar['open'] or bar['volume'] <= bar['avg_vol_intraday']:
                continue

            # regime thresholds
            regime, pct_thresh, pullback_pct = get_regime_at(t0, etfs)
            if (bar['high']/prev_close - 1) < pct_thresh:
                continue

            # find pullback bar
            later = df[df.index > t0]
            pb = later[
                (later['low'] <= bar['high']*(1-pullback_pct)) &
                (later['close'] < later['open']) &                     # red candle
                (later['volume'] < bar['volume'])                       # smaller volume
                ]
            if pb.empty:
                continue
            t_pb = pb.index[0]
            pb_low    = pb.loc[t_pb,'low']
            pb_volume = pb.loc[t_pb,'volume']

            # entry trigger above original high
            post_pb = df[df.index > t_pb]
            trig = post_pb[post_pb['close'] >= bar['high']]
            if trig.empty:
                continue
            t_ent = trig.index[0]
            ent   = df.loc[t_ent]

            # entry bar must be green and volume > pullback volume
            if ent['close'] <= ent['open'] or ent['volume'] <= pb_volume:
                continue

            # ATR-based stop & size
            stop_price    = min(pb_low, ent['close'] - ent['atr14'])
            risk_per_share= ent['close'] - stop_price
            risk_amount   = bankroll * RISK_PCT.get(regime, RISK_PCT['neutral'])
            shares        = max(1, math.floor(risk_amount / risk_per_share))

            signals.append({
                'ticker':      sym,
                'timestamp':   t_ent,
                'signal':      1,
                'entry_price': ent['close'],
                'stop_price':  round(stop_price,4),
                'regime':      regime,
                'shares':      shares
            })
            used_breaks.add(t0)

    os.makedirs(SIGNALS_DIR, exist_ok=True)
    out = os.path.join(SIGNALS_DIR, f'signals_{date_str}.csv')
    pd.DataFrame(signals).to_csv(out, index=False)
    print(f"✅ Wrote {len(signals)} signals → {out}")

if __name__ == '__main__':
    args = parse_args()
    generate_signals(args.date, args.bankroll)
