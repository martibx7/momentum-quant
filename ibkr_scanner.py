# === ibkr_scanner.py (updated with per‑minute relative volume) ===
from ib_insync import IB, ScannerSubscription, Stock, util
import pandas as pd
import datetime
import time
import os

# --- Configuration ---
MIN_PRICE = 2.0
MAX_PRICE = 20.0

# ⬇️ NEW: minute‑scale volume surge detection
AVG_MINUTES_PER_DAY = 390               # regular US session
MINUTE_RV_THRESHOLD   = 5.0             # 5× average minute volume
MIN_ABSOLUTE_VOLUME   = 500_000         # at least 500 k shares traded on the day so far
PCT_GAIN_THRESHOLD    = 0.02            # > 2 % intraday gain

LOG_FILE  = 'scanner_log.csv'
IB_HOST   = '127.0.0.1'
IB_PORT   = 7497
CLIENT_ID = 2
SCAN_INTERVAL = 60  # seconds

# --- Helper functions ---

def fetch_avg_volume(ib: IB, symbol: str) -> float | None:
    """30‑day average DAILY volume"""
    contract = ib.qualifyContracts(Stock(symbol, 'SMART', 'USD'))[0]
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='30 D',
        barSizeSetting='1 day',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=1
    )
    df = util.df(bars)
    return df['volume'].mean() if not df.empty else None


def fetch_today_bars(ib: IB, symbol: str) -> pd.DataFrame | None:
    """All 1‑minute bars so far today."""
    contract = ib.qualifyContracts(Stock(symbol, 'SMART', 'USD'))[0]
    today = datetime.date.today().strftime('%Y%m%d')
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=f"{today} 23:59:00",
        durationStr='1 D',
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=1
    )
    return util.df(bars) if bars else None


def scan_market(ib: IB):
    now = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"\n🕒 Scanning at {now} …")

    sub = ScannerSubscription(
        instrument='STK',
        locationCode='STK.US',
        scanCode='TOP_PERC_GAIN',
        abovePrice=MIN_PRICE,
        belowPrice=MAX_PRICE,
        numberOfRows=200
    )
    results = ib.reqScannerSubscription(sub)
    print(f"↪️ {len(results)} tickers returned by IB scanner")

    rows = []
    for res in results:
        symbol = res.contractDetails.contract.symbol
        try:
            avg_daily_vol = fetch_avg_volume(ib, symbol)
            if not avg_daily_vol:
                continue

            df_today = fetch_today_bars(ib, symbol)
            if df_today is None or df_today.empty:
                continue

            # --- Price metrics
            open_px    = df_today.iloc[0]['open']
            curr_px    = df_today.iloc[-1]['close']
            pct_gain   = (curr_px - open_px) / open_px

            # --- Volume metrics
            day_vol          = df_today['volume'].sum()
            last_minute_vol  = df_today.iloc[-1]['volume']
            avg_minute_vol   = avg_daily_vol / AVG_MINUTES_PER_DAY
            rel_vol_minute   = last_minute_vol / avg_minute_vol if avg_minute_vol else 0.0

            # --- Trigger condition (minute‑scale surge)
            if (rel_vol_minute >= MINUTE_RV_THRESHOLD and
                    day_vol       >= MIN_ABSOLUTE_VOLUME and
                    pct_gain      >= PCT_GAIN_THRESHOLD):

                ts = datetime.datetime.now()
                print(f"⚡ {symbol}: +{pct_gain:.2%}  RVₘ={rel_vol_minute:.1f}  Vol={day_vol//1_000}k")
                rows.append({
                    'timestamp': ts,
                    'symbol': symbol,
                    'open': open_px,
                    'current': curr_px,
                    'pct_gain': pct_gain,
                    'day_vol': day_vol,
                    'last_min_vol': last_minute_vol,
                    'rv_minute': rel_vol_minute,
                    'avg_daily_vol': avg_daily_vol,
                })
        except Exception as e:
            print(f"Error on {symbol}: {e}")

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(LOG_FILE, mode='a', header=not os.path.exists(LOG_FILE), index=False)


def main():
    ib = IB()
    while not ib.isConnected():
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
        except Exception:
            print("Retrying IB connection in 5 s …")
            time.sleep(5)
    print("Connected to IB Gateway/TWS ✅")

    try:
        while True:
            scan_market(ib)
            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print("Interrupted by user, shutting down …")
    finally:
        ib.disconnect()

if __name__ == '__main__':
    main()
