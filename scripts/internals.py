#!/usr/bin/env python3
"""
internals.py

Fetch historical minute bars for market-internals and broad ETFs
using Polygon.io, over a specified date or date range.

Outputs CSVs in data/internals/:
  - add_<start>_<end>.csv     (A/D line)
  - tick_<start>_<end>.csv    (TICK index)
  - vix_<start>_<end>.csv     (VIX index)
  - spy_<start>_<end>.csv     (SPY ETF)
  - qqq_<start>_<end>.csv     (QQQ ETF)
  - uvol_<start>_<end>.csv    (Up-Volume)
  - dvol_<start>_<end>.csv    (Down-Volume)
  - vold_<start>_<end>.csv    (Total Dollar Volume)

Usage:
  python internals.py --start YYYY-MM-DD [--end YYYY-MM-DD]

If --end is omitted, end = start (single-day snapshot).
"""
import os
import time
import argparse
import requests
import pandas as pd
from dotenv import load_dotenv

# Load POLYGON_API_KEY from .env
load_dotenv()
API_KEY = os.getenv('POLYGON_API_KEY')
if not API_KEY:
    raise RuntimeError('Please set POLYGON_API_KEY in your .env')
BASE = 'https://api.polygon.io'
# Minimum delay between calls (sec) to avoid rate limits
CALL_DELAY = 0.3

# Symbols to fetch
SYMBOLS = {
    'add': '$ADD',      # Advance/Decline Line
    'tick': '$TICK',    # NYSE Tick
    'vix': 'VIX',       # CBOE Volatility Index
    ## 'spy': 'SPY',       # SPDR S&P 500 ETF
    ## 'qqq': 'QQQ',       # Invesco QQQ ETF
    'uvol': '$UVOL',    # Up-Volume
    'dvol': '$DVOL',    # Down-Volume
    'vold': '$VOLD'     # Total Dollar Volume
}


def fetch_minute_bars(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch 1-minute bars for symbol from start to end inclusive.
    Dates as 'YYYY-MM-DD'.
    """
    url = f"{BASE}/v2/aggs/ticker/{symbol}/range/1/minute/{start}/{end}"
    params = {
        'apiKey': API_KEY,
        'unadjusted': 'true',
        'sort': 'asc'
    }
    resp = requests.get(url, params=params)
    if resp.status_code == 429:
        # rate limit hit
        raise requests.exceptions.HTTPError(
            f"Rate limit exceeded for {symbol}", response=resp
        )
    resp.raise_for_status()
    results = resp.json().get('results', [])
    if not results:
        # no bars returned (likely not available on free tier)
        return pd.DataFrame()
    df = pd.DataFrame(results)
    df['timestamp'] = pd.to_datetime(df['t'], unit='ms')
    df = df.set_index('timestamp')
    df = df.rename(columns={'o':'open','h':'high','l':'low','c':'close','v':'volume'})
    return df[['open','high','low','close','volume']]


def main():
    p = argparse.ArgumentParser(description='Fetch market internals via Polygon')
    p.add_argument('--start', required=True, help='Start date YYYY-MM-DD')
    p.add_argument('--end', help='End date YYYY-MM-DD (inclusive)')
    args = p.parse_args()
    start = args.start
    end = args.end or start
    out_dir = os.path.join('data', 'internals')
    os.makedirs(out_dir, exist_ok=True)

    for name, sym in SYMBOLS.items():
        print(f"Fetching {sym} from {start} to {end}…", end=' ')
        try:
            df = fetch_minute_bars(sym, start, end)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print('rate-limited, skipping')
                time.sleep(CALL_DELAY)
                continue
            else:
                print(f"error: {e}")
                time.sleep(CALL_DELAY)
                continue
        if df.empty:
            print('no data (not available/free tier)')
        else:
            fname = f"{name}_{start}_{end}.csv"
            path = os.path.join(out_dir, fname)
            df.to_csv(path)
            print(f"saved → {path}")
        # throttle to avoid rate limits
        time.sleep(CALL_DELAY)

if __name__ == '__main__':
    main()
