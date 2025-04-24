#!/usr/bin/env python3
"""
universe_filter.py

Static universe screen for a given backtest date, using embedded metadata:
 1) Load tickers from data/universe.txt
 2) Find which have processed bars for the date
 3) For each symbol:
    - Today's % change vs prev_close (embedded)
    - today's total volume ≥ 5M
    - relative volume (today_vol / avg_daily_vol) ≥ 5×
 4) Write passing tickers to CSV and log all failures

Usage:
  python universe_filter.py --date YYYY-MM-DD
"""
import os
import argparse
import pandas as pd
from glob import glob

# CONFIG
UNIVERSE_FILE     = os.path.join('data', 'universe.txt')
PROCESSED_DIR     = os.path.join('data', 'processed')
SIGNALS_DIR       = os.path.join('data', 'signals')

# CONFIG — Bull‐market settings (looser)
PCT_MOVE_THRESH   = 0.05        #  5% intraday move
RV_THRESH         = 2.0         #  2× avg daily volume
TODAY_VOL_THRESH  = 1_000_000   # 1M shares


def parse_args():
    p = argparse.ArgumentParser(description="Filter static universe by date")
    p.add_argument('--date', required=True, help='Date in YYYY-MM-DD format')
    return p.parse_args()


def load_universe():
    with open(UNIVERSE_FILE) as f:
        return [line.strip().upper() for line in f if line.strip()]


def main():
    args     = parse_args()
    date_str = args.date
    os.makedirs(SIGNALS_DIR, exist_ok=True)

    # prepare log
    log_path = os.path.join(SIGNALS_DIR, f'universe_filter_{date_str}.log')
    log = open(log_path, 'w')
    log.write(f"Universe filter run for {date_str}\n\n")

    # 1) static universe
    universe = set(load_universe())

    # 2) find processed symbols
    pattern    = os.path.join(PROCESSED_DIR, f'*_{date_str}.csv')
    proc_files = glob(pattern)
    proc_syms  = {os.path.basename(f).split('_')[0] for f in proc_files}
    symbols    = sorted(universe & proc_syms)

    print(f"🔍 Screening {len(symbols)} symbols for {date_str}...")
    log.write(f"Candidates ({len(symbols)}): {symbols}\n\n")

    passed = []
    for sym in symbols:
        reasons = []
        path = os.path.join(PROCESSED_DIR, f"{sym}_{date_str}.csv")

        # load
        try:
            df = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
        except Exception as e:
            reasons.append(f"failed to read file ({e})")
            log.write(f"{sym}: {', '.join(reasons)}\n")
            continue

        # need our embedded metadata
        if 'prev_close' not in df.columns or 'avg_daily_vol' not in df.columns:
            reasons.append("missing prev_close or avg_daily_vol")
            log.write(f"{sym}: {', '.join(reasons)}\n")
            continue

        # 1) % move vs prev_close
        prev = df['prev_close'].iloc[0]
        if pd.isna(prev):
            reasons.append("prev_close is NaN")
        else:
            intrahigh = df['high'].max()
            pct_move  = intrahigh / prev - 1
            if pct_move < PCT_MOVE_THRESH:
                reasons.append(f"pct_move {pct_move:.2%} < {PCT_MOVE_THRESH:.2%}")

        # 2) today's total volume
        today_vol = df['volume'].sum()
        if today_vol < TODAY_VOL_THRESH:
            reasons.append(f"today_vol {today_vol} < {TODAY_VOL_THRESH}")

        # 3) relative volume vs avg_daily_vol
        avg_vol = df['avg_daily_vol'].iloc[0]
        if pd.isna(avg_vol) or avg_vol <= 0:
            reasons.append("avg_daily_vol is missing or zero")
        else:
            rel_vol = today_vol / avg_vol
            if rel_vol < RV_THRESH:
                reasons.append(f"rel_vol {rel_vol:.2f} < {RV_THRESH}")

        # record outcome
        if reasons:
            log.write(f"{sym}: {', '.join(reasons)}\n")
        else:
            passed.append({
                'ticker': sym,
                'pct_move': round(pct_move, 4),
                'today_vol': int(today_vol),
                'avg_daily_vol': int(avg_vol),
                'rel_vol': round(rel_vol, 2),
            })

    # dump CSV of passers
    df_pass = pd.DataFrame(passed)
    out_csv = os.path.join(SIGNALS_DIR, f'universe_filtered_{date_str}.csv')
    df_pass.to_csv(out_csv, index=False)

    log.write(f"\nPassed: {len(passed)} symbols\n")
    log.close()

    print(f"✅ {len(passed)} symbols passed filters → {out_csv}")
    print(f"📝 Detailed log → {log_path}")


if __name__ == '__main__':
    main()
