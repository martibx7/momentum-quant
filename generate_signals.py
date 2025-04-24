#!/usr/bin/env python3
"""
generate_signals.py  (UPDATED)

Scan processed 1-min bars for symbols against dynamic ETF barometer regime.
Uses embedded metadata (prev_close, avg_daily_vol) and removes live API calls.

Usage:
  python generate_signals.py --date YYYY-MM-DD

Outputs:
  data/signals/signals_<date>.csv
"""
import os
import argparse
import pandas as pd
from glob import glob

# CONFIG
PROCESSED_DIR   = os.path.join('data', 'processed')
SIGNALS_DIR     = os.path.join('data', 'signals')
BASE_PCT_MOVE   = 0.10   # 10% baseline
BASE_PULLBACK   = 0.005  # 0.5% baseline
BAR_ETFS        = [('SPY',1), ('QQQ',1), ('IWM',2)]

def parse_args():
    p = argparse.ArgumentParser(description='Generate entry signals by date')
    p.add_argument('--date', required=True, help='YYYY-MM-DD')
    return p.parse_args()

def load_processed(sym, date_str):
    """Load processed minute bars and metadata for a given symbol."""
    path = os.path.join(PROCESSED_DIR, f"{sym}_{date_str}.csv")
    df = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
    # ensure metric columns exist
    if 'prev_close' not in df.columns or 'avg_daily_vol' not in df.columns:
        raise KeyError(f"{sym} missing metadata columns")
    df = df.dropna(subset=['high','low','open','close','volume'])
    # intraday rolling avg volume for entry logic
    df['avg_vol_intraday'] = df['volume'].rolling(30, min_periods=1).mean()
    # cumulative volume for regime
    df['cum_vol'] = df['volume'].cumsum()
    # cumulative high for breakout detection
    df['cum_high'] = df['high'].cummax()
    return df

def load_barometers(date_str):
    """Load minute bars and metadata for all barometer ETFs."""
    etfs = {}
    for sym, weight in BAR_ETFS:
        df = load_processed(sym, date_str)
        etfs[sym] = {
            'df': df,
            'avg_daily_vol': df['avg_daily_vol'].iloc[0],
        }
    return etfs

def get_regime_at(t0, etfs):
    """Compute regime label, dynamic pct_move & pullback thresholds at timestamp t0."""
    score = 0
    for sym, weight in BAR_ETFS:
        df = etfs[sym]['df']
        slice_ = df[df.index <= t0]
        if slice_.empty:
            continue
        # price change
        first = slice_['close'].iloc[0]
        last  = slice_['close'].iloc[-1]
        if (last/first - 1) > 0:
            score += weight
        # rel vol vs daily avg
        if slice_['cum_vol'].iloc[-1] >= etfs[sym]['avg_daily_vol']:
            score += weight
    # thresholds
    if score >= 5:
        return 'strong_bull', BASE_PCT_MOVE*0.8, BASE_PULLBACK*0.6
    if score >= 3:
        return 'neutral', BASE_PCT_MOVE, BASE_PULLBACK
    return 'bearish', BASE_PCT_MOVE*1.2, BASE_PULLBACK*1.4

def generate_signals(date_str):
    # load barometers once
    etfs = load_barometers(date_str)

    # gather all symbols with processed data
    paths = glob(os.path.join(PROCESSED_DIR, f'*_{date_str}.csv'))
    symbols = sorted({os.path.basename(p).split('_')[0] for p in paths if not p.startswith(('SPY','QQQ','IWM'))})

    signals = []
    print(f"🔍 Generating signals for {len(symbols)} symbols on {date_str}...")
    for sym in symbols:
        df = load_processed(sym, date_str)
        prev_close = df['prev_close'].iloc[0]
        if pd.isna(prev_close) or prev_close <= 0:
            continue

        used_breaks = set()
        # scan for new-high green bars
        for t0, row in df[df['high'] == df['cum_high']].iterrows():
            if t0 in used_breaks:
                continue
            if row['close'] <= row['open'] or row['volume'] <= row['avg_vol_intraday']:
                continue

            # regime and dynamic thresholds
            regime, pct_move_thresh, pullback_pct = get_regime_at(t0, etfs)
            # initial high move check vs prev_close
            if (row['high']/prev_close - 1) < pct_move_thresh:
                continue

            # find micro pullback
            later = df[df.index > t0]
            pb = later[later['low'] <= row['high'] * (1 - pullback_pct)]
            if pb.empty:
                continue
            t_pb = pb.index[0]
            pb_low = pb.loc[t_pb,'low']

            # entry trigger when price crosses back above high
            post_pb = df[df.index > t_pb]
            trig = post_pb[post_pb['close'] >= row['high']]
            if trig.empty:
                continue
            t_ent = trig.index[0]
            ent = df.loc[t_ent]

            # indicator checks
            if not (ent['macd'] > ent['macd_signal']
                    and ent['close'] > ent['vwap']
                    and ent['ema9'] > ent['ema20'] > ent['ema200']):
                continue

            # compute stop
            stop_price = min(pb_low, ent['close'] - ent['atr14'])

            signals.append({
                'ticker':      sym,
                'timestamp':   t_ent,
                'signal':      1,
                'entry_price': ent['close'],
                'stop_price':  round(stop_price,4),
                'regime':      regime
            })
            used_breaks.add(t0)

    # save
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    out = os.path.join(SIGNALS_DIR, f'signals_{date_str}.csv')
    pd.DataFrame(signals).to_csv(out, index=False)
    print(f"✅ Wrote {len(signals)} signals → {out}")

if __name__ == '__main__':
    args = parse_args()
    generate_signals(args.date)

