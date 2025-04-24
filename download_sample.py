# download_sample.py

import os
import yfinance as yf
from datetime import datetime

SYMBOLS = ['AAPL', 'MSFT', 'SPY']
OUT_DIR = os.path.join('data', 'raw')
os.makedirs(OUT_DIR, exist_ok=True)

for sym in SYMBOLS:
    # Pull the last 1 trading day at 1-minute resolution
    df = yf.download(
        tickers=sym,
        period='1d',
        interval='1m',
        progress=False
    )
    if df.empty:
        print(f"❌ No data for {sym}")
        continue

    # Reformat for our ingestion script
    df.reset_index(inplace=True)  # makes 'Datetime' a column
    path = os.path.join(OUT_DIR, f"{sym}_1min_sample.csv")
    df.to_csv(path, index=False)
    print(f"✅ Saved sample for {sym} → {path}")
