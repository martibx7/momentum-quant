#!/usr/bin/env python3
"""
universe_filter_updated.py

Back‑test universe screener that now mirrors the live IBKR scanner’s
**per‑minute relative‑volume** logic while keeping all of the original
filters (pct‑move, total‑volume, whole‑day RV).

How it works (per symbol on a given back‑test date):
 1. Loads intraday 1‑min bars that were pre‑processed into
    data/processed/SYMBOL_YYYY‑MM‑DD.csv
 2. Computes intraday high %‑move vs previous close (needs
    `prev_close` column in the file)
 3. Computes *total* volume for the day and standard relative volume
    (total_vol / avg_daily_vol)
 4. **NEW** – computes the stock’s average *minute* volume
       avg_minute_vol = avg_daily_vol / TRADING_MINUTES
    then checks whether **any** 1‑min bar shows volume ≥
       MINUTE_RV_THRESH × avg_minute_vol
 5. A symbol passes if:
       – %‑move >= PCT_MOVE_THRESH, AND
       – today_vol >= TODAY_VOL_THRESH, AND
       – ( whole‑day_rel_vol >= RV_THRESH  OR  minute_spike_detected )
 6. Results written to data/signals/universe_filtered_DATE.csv

Usage:
  python universe_filter_updated.py --date YYYY‑MM‑DD
"""

import os, argparse
import pandas as pd
from glob import glob

# ------------------------------------------------------------- CONFIG ---
UNIVERSE_FILE   = os.path.join('data', 'universe.txt')
PROCESSED_DIR   = os.path.join('data', 'processed')
SIGNALS_DIR     = os.path.join('data', 'signals')

# Strategy thresholds (tuned for momentum day‑trading back‑test)
PCT_MOVE_THRESH     = 0.05      # 5 % intraday high vs prev close
TODAY_VOL_THRESH    = 1_000_000 # 1 M shares absolute volume floor
RV_THRESH           = 2.0       # whole‑day relative volume ≥ 2×

# ---- NEW per‑minute relative‑volume parameters ------------------------
TRADING_MINUTES     = 390       # regular US session
MINUTE_RV_THRESH    = 5.0       # minute bar ≥ 5× avg minute volume

# -----------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Filter universe by date, with per‑minute RV surge detection")
    p.add_argument('--date', required=True, help='Back‑test date YYYY‑MM‑DD')
    return p.parse_args()


def load_universe():
    with open(UNIVERSE_FILE) as f:
        return [line.strip().upper() for line in f if line.strip()]


def main():
    args      = parse_args()
    date_str  = args.date
    os.makedirs(SIGNALS_DIR, exist_ok=True)

    # log setup
    log_path  = os.path.join(SIGNALS_DIR, f'universe_filter_{date_str}.log')
    log       = open(log_path, 'w')
    log.write(f"Universe filter run for {date_str}\n\n")

    # universe filenames present for the day
    universe  = set(load_universe())
    pattern   = os.path.join(PROCESSED_DIR, f'*_{date_str}.csv')
    files     = glob(pattern)
    candidates= {os.path.basename(f).split('_')[0] for f in files}
    symbols   = sorted(universe & candidates)

    print(f"🔍 Screening {len(symbols)} symbols for {date_str}…")
    log.write(f"Candidates ({len(symbols)}): {symbols}\n\n")

    passed = []
    for sym in symbols:
        reasons = []
        path    = os.path.join(PROCESSED_DIR, f"{sym}_{date_str}.csv")

        # --- load minute bars ---
        try:
            df = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
        except Exception as e:
            log.write(f"{sym}: file read error ({e})\n")
            continue

        # metadata check
        if 'prev_close' not in df.columns or 'avg_daily_vol' not in df.columns:
            log.write(f"{sym}: missing prev_close or avg_daily_vol\n")
            continue

        prev_close = df['prev_close'].iloc[0]
        avg_daily  = df['avg_daily_vol'].iloc[0]
        if pd.isna(prev_close) or pd.isna(avg_daily) or avg_daily <= 0:
            log.write(f"{sym}: bad prev_close / avg_daily_vol\n")
            continue

        # -------- Feature calculations -----------------------------------
        intrahigh     = df['high'].max()
        pct_move      = intrahigh / prev_close - 1

        today_vol     = df['volume'].sum()
        rel_vol_day   = today_vol / avg_daily

        avg_min_vol   = avg_daily / TRADING_MINUTES
        minute_spike  = (df['volume'] >= avg_min_vol * MINUTE_RV_THRESH).any()

        # -------- Filter decisions ---------------------------------------
        if pct_move < PCT_MOVE_THRESH:
            reasons.append(f"pct_move {pct_move:.2%} < {PCT_MOVE_THRESH:.2%}")
        if today_vol < TODAY_VOL_THRESH:
            reasons.append(f"today_vol {today_vol} < {TODAY_VOL_THRESH}")
        if not (rel_vol_day >= RV_THRESH or minute_spike):
            reasons.append("no RV signal (day_RV < thresh and no minute spike)")

        if reasons:
            log.write(f"{sym}: {', '.join(reasons)}\n")
            continue

        passed.append({
            'ticker'       : sym,
            'pct_move'     : round(pct_move, 4),
            'today_vol'    : int(today_vol),
            'avg_daily_vol': int(avg_daily),
            'rel_vol_day'  : round(rel_vol_day, 2),
            'minute_spike' : minute_spike,
        })

    # -------- Output -----------------------------------------------------
    df_pass = pd.DataFrame(passed)
    out_csv = os.path.join(SIGNALS_DIR, f'universe_filtered_{date_str}.csv')
    df_pass.to_csv(out_csv, index=False)

    log.write(f"\nPassed: {len(passed)} symbols\n")
    log.close()

    print(f"✅ {len(passed)} symbols passed filters → {out_csv}")
    print(f"📝 Detailed log → {log_path}")


if __name__ == '__main__':
    main()
