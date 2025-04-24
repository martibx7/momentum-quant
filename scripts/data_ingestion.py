#!/usr/bin/env python3
"""
Fetch historical bar data from IBKR if possible,
otherwise load from sample CSVs in data/raw/.
"""

import os
from datetime import datetime
import pandas as pd
from ib_insync import util, Stock, IB
from dotenv import load_dotenv

# ==== USER CONFIGURATION ====
SYMBOLS      = ['AAPL', 'MSFT', 'SPY']
DURATION     = '1 D'       # last 1 day
BAR_SIZE     = '1 min'     # 1-minute bars
WHAT_TO_SHOW = 'TRADES'
USE_RTH      = True
# ============================

def connect_ibkr():
    """Try to connect to IBKR; raises if unavailable."""
    load_dotenv()
    ib = IB()
    host = os.getenv('IB_HOST', '127.0.0.1')
    port = int(os.getenv('IB_PORT', 7497))
    cid  = int(os.getenv('IB_CLIENT_ID', 1))
    ib.connect(host, port, clientId=cid)
    return ib

def fetch_and_save_live(ib, sym):
    """Fetch from IBKR and save timestamped CSV."""
    c = Stock(sym, 'SMART', 'USD')
    bars = ib.reqHistoricalData(
        c, endDateTime='',
        durationStr=DURATION,
        barSizeSetting=BAR_SIZE,
        whatToShow=WHAT_TO_SHOW,
        useRTH=USE_RTH,
        formatDate=1
    )
    df = util.df(bars)
    if df.empty:
        print(f"⚠️ No live data for {sym}")
        return False

    out = os.path.join('data', 'raw')
    os.makedirs(out, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    clean = BAR_SIZE.replace(' ', '')
    fname = f"{sym}_{clean}_{ts}.csv"
    df.to_csv(os.path.join(out, fname), index=False)
    print(f"✅ Live data saved: {fname}")
    return True

def fallback_sample(sym):
    """Load sample CSV and re-save with a timestamped name."""
    in_path = os.path.join('data', 'raw', f"{sym}_1min_sample.csv")
    if not os.path.isfile(in_path):
        print(f"❌ Sample missing for {sym}: {in_path}")
        return

    df = pd.read_csv(in_path)
    out = os.path.join('data', 'raw')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f"{sym}_1min_offline_{ts}.csv"
    df.to_csv(os.path.join(out, fname), index=False)
    print(f"✅ Offline data copied: {fname}")

def main():
    try:
        ib = connect_ibkr()
        print("💡 Connected to IBKR — pulling live data")
        for s in SYMBOLS:
            if not fetch_and_save_live(ib, s):
                fallback_sample(s)
        ib.disconnect()
    except Exception as e:
        print(f"⚠️ Live fetch failed ({e}); using offline samples")
        for s in SYMBOLS:
            fallback_sample(s)

if __name__ == '__main__':
    main()
