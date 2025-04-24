#!/usr/bin/env python3
"""
Spot‐check your generated signal CSVs.

Usage:
  # Check specific tickers:
  python scripts/spot_checker.py GME AAPL MSFT

  # Or, if you pass no args, it will randomly spot‐check 5 files:
  python scripts/spot_checker.py
"""
import os
import sys
import random
import pandas as pd

# where your *_signals.csv live
SIGNALS_DIR = os.path.join('data', 'signals')

def list_all_symbols():
    """Return all symbols for which we have a signals CSV."""
    files = os.listdir(SIGNALS_DIR)
    return sorted(f.split('_signals.csv')[0]
                  for f in files
                  if f.endswith('_signals.csv'))

def load_signals(sym):
    path = os.path.join(SIGNALS_DIR, f"{sym}_signals.csv")
    df = pd.read_csv(path, parse_dates=['timestamp'])
    return df

def spot_check(sym):
    try:
        df = load_signals(sym)
    except FileNotFoundError:
        print(f"[!] {sym}: signals file not found.")
        return

    print(f"\n=== {sym} ===")
    print("First 5 rows:")
    print(df.head(5).to_string(index=False))
    nonzero = df[df['signal'] != 0]
    if nonzero.empty:
        print("→ No non-zero signals in this file.\n")
    else:
        print("\nNon-zero signals:")
        print(nonzero.head(5).to_string(index=False))
        print()

def main():
    # get symbols from args or pick 5 at random
    args = sys.argv[1:]
    all_syms = list_all_symbols()

    if args:
        to_check = args
    else:
        to_check = random.sample(all_syms, min(5, len(all_syms)))
        print(f"Spot‐checking (random) {to_check}\n")

    for sym in to_check:
        spot_check(sym)

if __name__ == '__main__':
    main()
