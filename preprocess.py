#!/usr/bin/env python3
"""
preprocess.py

Load raw CSVs for a specified date (or list available dates), normalize to 1-min bars,
filter by price band, and compute all our indicators: VWAP, EMAs, MACD, ATR, ADX, momentum.
Carry through embedded metadata columns: prev_close and avg_daily_vol.

Usage:
  # List available dates in data/raw:
  python preprocess.py --list-dates

  # Process a specific date (e.g. 2025-04-21):
  python preprocess.py --date 2025-04-21
"""

import os
import argparse
import pandas as pd
from glob import glob

def parse_args():
    p = argparse.ArgumentParser(description='Preprocess raw 1-min data by date')
    p.add_argument('--date', help='Date to process in YYYY-MM-DD format')
    p.add_argument('--list-dates', action='store_true', help='List available dates in raw data')
    return p.parse_args()

# ==== CONFIG ====
RAW_DIR    = os.path.join('data', 'raw')
OUT_DIR    = os.path.join('data', 'processed')
MIN_PRICE  = 2.0
MAX_PRICE  = 20.0
# =================

def extract_dates_from_raw():
    """Scan raw filenames and return sorted list of unique dates (YYYY-MM-DD)."""
    files = glob(os.path.join(RAW_DIR, "*_*.csv"))
    dates = set()
    for f in files:
        fname = os.path.basename(f)
        parts = fname.split('_')
        # expect SYMBOL_YYYYMMDD_...csv
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 8:
            d = parts[1]
            dates.add(f"{d[:4]}-{d[4:6]}-{d[6:]}" )
    return sorted(dates)

def find_symbols_for_date(date_str):
    """Return list of symbol prefixes for raw files matching the given date."""
    pattern = os.path.join(RAW_DIR, f"*_{date_str.replace('-','')}*.csv")
    files = glob(pattern)
    symbols = sorted({ os.path.basename(f).split('_',1)[0] for f in files })
    return symbols

def load_csv(path):
    df = pd.read_csv(path)
    # unify timestamp
    if 'Datetime' in df.columns:
        df['timestamp'] = pd.to_datetime(df['Datetime'])
        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'
        })
    else:
        # assume columns 'date' and 'time' exist
        df['timestamp'] = pd.to_datetime(df['date'] + ' ' + df['time'])
        df = df.rename(columns=str.lower)
    # preserve metadata if present
    metadata_cols = []
    for meta in ['prev_close', 'avg_daily_vol']:
        if meta in df.columns:
            metadata_cols.append(meta)
    # select required columns
    cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume'] + metadata_cols
    df = df[cols].set_index('timestamp')
    # ensure numeric
    for c in ['open', 'high', 'low', 'close', 'volume'] + metadata_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna(subset=['close'])

def preprocess_symbol(sym, date_str):
    print(f"→ Processing {sym} on {date_str}")
    filename_pattern = os.path.join(RAW_DIR, f"{sym}_{date_str.replace('-','')}*.csv")
    raws = glob(filename_pattern)
    if not raws:
        print(f"   ❌ No raw file for {sym} on {date_str}")
        return
    path = raws[-1]
    df = load_csv(path)

    # 1-min bars
    df = df.resample('1min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        **({ 'prev_close': 'first', 'avg_daily_vol': 'first' } if 'prev_close' in df.columns else {})
    }).ffill()

    # price filter
    df = df[(df['close'] >= MIN_PRICE) & (df['close'] <= MAX_PRICE)]
    if df.empty:
        print("   ⚠️ No bars in price band, skipping.")
        return

    # VWAP
    df['vwap'] = ((df['close'] + df['high'] + df['low'])/3 * df['volume']).cumsum() / df['volume'].cumsum()

    # EMAs
    df['ema9']   = df['close'].ewm(span=9, adjust=False).mean()
    df['ema20']  = df['close'].ewm(span=20, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

    # MACD & signal
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # True Range & ATR
    df['tr'] = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['atr14'] = df['tr'].rolling(14).mean()

    # ADX
    up = df['high'].diff()
    dn = df['low'].diff().abs()
    plus = up.where(up>0, 0)
    minus = dn.where(dn>0, 0)
    tr14 = df['tr'].rolling(14).sum()
    df['pdi'] = 100 * plus.rolling(14).sum() / tr14
    df['mdi'] = 100 * minus.rolling(14).sum() / tr14
    df['dx']  = 100 * (df['pdi'] - df['mdi']).abs() / (df['pdi'] + df['mdi'])
    df['adx14'] = df['dx'].rolling(14).mean()

    # momentum
    df['mom5']  = df['close'].pct_change(5)
    df['mom15'] = df['close'].pct_change(15)

    # persist
    os.makedirs(OUT_DIR, exist_ok=True)
    out_file = os.path.join(OUT_DIR, f"{sym}_{date_str}.csv")
    df.to_csv(out_file)
    print(f"   ✅ Wrote {out_file}")

def main():
    args = parse_args()
    if args.list_dates:
        dates = extract_dates_from_raw()
        print("Available dates:")
        for d in dates:
            print(f" - {d}")
        return

    if not args.date:
        print("❌ Please specify --date YYYY-MM-DD or use --list-dates")
        return

    date_str = args.date
    symbols = find_symbols_for_date(date_str)
    if not symbols:
        print(f"❌ No raw files found for {date_str}")
        return

    print(f"🔍 Found {len(symbols)} symbols for {date_str}: {symbols}")
    for s in symbols:
        preprocess_symbol(s, date_str)

if __name__ == '__main__':
    main()

