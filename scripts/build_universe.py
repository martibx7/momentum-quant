#!/usr/bin/env python3
import os
import pandas as pd

# 1. Load the holdings CSV, skipping metadata (header is row index 9)
csv_path = os.path.join('data', 'raw', 'IWM_holdings.csv')
df = pd.read_csv(csv_path, header=9)

# 2. Keep only actual equities
eq = df[df['Asset Class'].str.lower() == 'equity']

# 3. Pull out the tickers, uppercase & unique
tickers = eq['Ticker'].str.upper().unique().tolist()

# 4. Write to your static universe file
os.makedirs('data', exist_ok=True)
out_path = os.path.join('data', 'universe.txt')
with open(out_path, 'w') as f:
    f.write('\n'.join(tickers))

print(f"✅ Wrote {len(tickers)} Russell-2000 tickers to {out_path}")
