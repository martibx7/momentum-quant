#!/usr/bin/env python3
"""
download_universe_samples.py

Download 1-day, 1-minute sample CSVs for every ticker in
`data/universe.txt` plus SPY/QQQ/IWM for barometer use,
and embed previous close & 30-day average daily volume directly
into each raw minute-bar CSV.

Usage:
    python download_universe_samples.py --date YYYY-MM-DD
"""
import os
import time
import argparse
import datetime
import yfinance as yf

# CONFIG
UNIVERSE_PATH = os.path.join('data', 'universe.txt')
OUT_DIR_RAW   = os.path.join('data', 'raw')
INTERVAL      = '1m'
PAUSE_SEC     = 0.5
BAROMETERS    = ['SPY', 'QQQ', 'IWM']
HIST_DAYS     = 30

def load_universe(path=UNIVERSE_PATH):
    with open(path) as f:
        return [line.strip().upper() for line in f if line.strip()]

def parse_args():
    parser = argparse.ArgumentParser(
        description='Download 1-min bars and embed prev_close & avg_daily_vol'
    )
    parser.add_argument(
        '--date', required=True,
        help='Date to download in YYYY-MM-DD format'
    )
    return parser.parse_args()

def fetch_metadata(sym):
    """
    Returns (prev_close, avg_daily_vol) using up to HIST_DAYS+1 of daily history.
    """
    prev_close = None
    avg_daily_vol = 0
    try:
        hist = yf.Ticker(sym).history(period=f'{HIST_DAYS+1}d', interval='1d')
        hist = hist.dropna(subset=['Close', 'Volume'])
        if len(hist) >= 2:
            closes = hist['Close']
            prev_close = float(closes.iloc[-2])
            vols = hist['Volume'].iloc[:-1]
            avg_daily_vol = float(vols.mean())
    except Exception as e:
        print(f"⚠️ metadata fetch failed for {sym}: {e}")
    return prev_close, avg_daily_vol

def download_symbol(sym, start, end, date_str):
    """
    Download minute bars, fetch metadata, embed into DataFrame, and write CSV.
    """
    try:
        df = yf.download(
            tickers=sym,
            start=start,
            end=end,
            interval=INTERVAL,
            progress=False
        )
    except Exception as e:
        print(f"⚠️ Error downloading minute data for {sym}: {e}")
        return

    if df.empty:
        print(f"❌ No minute data for {sym} on {date_str}")
        return

    prev_close, avg_daily_vol = fetch_metadata(sym)

    df['prev_close'] = prev_close
    df['avg_daily_vol'] = avg_daily_vol

    # save augmented raw CSV
    df.reset_index(inplace=True)
    fname = f"{sym}_{date_str}_1min.csv"
    path = os.path.join(OUT_DIR_RAW, fname)
    if not os.path.isfile(path):
        df.to_csv(path, index=False)
        print(f"✅ Saved {sym} → {fname} (prev_close={prev_close}, avg_daily_vol={int(avg_daily_vol)})")
    else:
        print(f"⏭ {fname} exists, skipping")

def main():
    args = parse_args()
    try:
        date_obj = datetime.datetime.strptime(args.date, '%Y-%m-%d').date()
    except ValueError:
        print("❌ Invalid date format, use YYYY-MM-DD")
        return

    start = date_obj.isoformat()
    end = (date_obj + datetime.timedelta(days=1)).isoformat()
    date_str = date_obj.strftime('%Y%m%d')

    os.makedirs(OUT_DIR_RAW, exist_ok=True)

    universe = load_universe()
    full_list = sorted(set(universe + BAROMETERS))
    print(f"→ Downloading {len(full_list)} symbols for {args.date}…")

    for i, sym in enumerate(full_list, 1):
        download_symbol(sym, start, end, date_str)
        time.sleep(PAUSE_SEC)
        if i % 100 == 0:
            print("⏳ Pausing to respect rate limits…")
            time.sleep(2)

if __name__ == '__main__':
    main()

