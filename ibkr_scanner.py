from ib_insync import IB, ScannerSubscription, Stock, util
import pandas as pd
import datetime
import time
import os

# --- Configuration ---
MIN_PRICE = 2.0
MAX_PRICE = 20.0
# Loosened thresholds for diagnostic testing
REL_VOL_THRESHOLD = 1.0   # any uptick in volume
PCT_GAIN_THRESHOLD = 0.0  # any positive move

LOG_FILE = 'scanner_log.csv'
IB_HOST = '127.0.0.1'
IB_PORT = 7497
CLIENT_ID = 2
SCAN_INTERVAL = 60  # seconds

# --- Helper functions ---

def fetch_avg_volume(ib: IB, symbol: str) -> float | None:
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


def fetch_today_metrics(ib: IB, symbol: str):
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
    df = util.df(bars)
    if df.empty:
        return None, None, None
    open_price = df.iloc[0]['open']
    current_price = df.iloc[-1]['close']
    current_volume = df['volume'].sum()
    return open_price, current_price, current_volume


def scan_market(ib: IB):
    # Heartbeat: indicate a scan cycle
    now = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"🕒 Scanning at {now}...")

    # Request top percent gainers
    subscription = ScannerSubscription(
        instrument='STK',
        locationCode='STK.US',
        scanCode='TOP_PERC_GAIN',
        abovePrice=MIN_PRICE,
        belowPrice=MAX_PRICE,
        numberOfRows=200
    )
    results = ib.reqScannerSubscription(subscription)

    # Diagnostic: how many tickers returned?
    print(f"  ↪️ {len(results)} tickers returned by IB scanner")
    # Show raw top 5 results
    for res in results[:5]:
        sym = res.contractDetails.contract.symbol
        gain = getattr(res, 'rankValue', None)
        print(f"    Raw: {sym}  Gain={gain}")

    log_rows = []
    for res in results:
        symbol = res.contractDetails.contract.symbol
        try:
            avg_vol = fetch_avg_volume(ib, symbol)
            if not avg_vol:
                continue
            open_price, curr_price, curr_vol = fetch_today_metrics(ib, symbol)
            if open_price is None:
                continue
            rel_vol = curr_vol / avg_vol
            pct_gain = (curr_price - open_price) / open_price

            if rel_vol >= REL_VOL_THRESHOLD and pct_gain >= PCT_GAIN_THRESHOLD:
                timestamp = datetime.datetime.now()
                print(f"Signal: {symbol} | PctGain: {pct_gain:.2%} | RelVol: {rel_vol:.2f}")
                log_rows.append({
                    'timestamp': timestamp,
                    'symbol': symbol,
                    'open': open_price,
                    'current': curr_price,
                    'avg_vol': avg_vol,
                    'curr_vol': curr_vol,
                    'rel_vol': rel_vol,
                    'pct_gain': pct_gain
                })
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")

    if log_rows:
        df = pd.DataFrame(log_rows)
        header = not os.path.exists(LOG_FILE)
        df.to_csv(LOG_FILE, mode='a', header=header, index=False)


def main():
    ib = IB()
    print(f"Connecting to IB at {IB_HOST}:{IB_PORT}...")
    while not ib.isConnected():
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
        except Exception as e:
            print(f"Connection failed: {e}. Retrying in 5s...")
            time.sleep(5)
    print("Connected to IB Gateway/TWS")

    try:
        while True:
            scan_market(ib)
            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print("Interrupted by user, shutting down.")
    finally:
        ib.disconnect()

if __name__ == '__main__':
    main()
