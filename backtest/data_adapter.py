import glob
import datetime as dt
import pathlib
import pandas as pd
import collections

# Minimal stand-in for IB's BarData
BarData = collections.namedtuple(
    "BarData", ["time", "open", "high", "low", "close", "volume"]
)

class CSVDataProvider:
    """
    Loads minute-bars from data/raw/{symbol}_{YYYYMMDD}_1min.csv,
    uses the embedded prev_close column, and mimics the minimal IB
    client API your engines expect.
    """
    def __init__(self, repo_root: pathlib.Path, date_str: str):
        self.repo        = repo_root
        self.date_str    = date_str      # e.g. "20250430"
        self.cache       = {}            # symbol -> DataFrame
        self._current_ts = None          # for real-time bars

        # stub out .ib so any code doing `broker.ib.connect` or `.reqAccountSummary` still won’t blow up
        self.ib = self

        # Dummy event handlers for BrokerAPI subscriptions
        class DummyEvent:
            def __iadd__(self, other): return self
            def __isub__(self, other): return self

        self.execDetailsEvent = DummyEvent()
        self.orderStatusEvent = DummyEvent()
        self.openOrderEvent   = DummyEvent()
        self.nextValidIdEvent = DummyEvent()

    def load_minute_bars(self, symbol: str) -> pd.DataFrame:
        if symbol not in self.cache:
            pattern = str(self.repo / "data" / "raw" / f"{symbol}_{self.date_str}_1min.csv")
            matches = glob.glob(pattern)
            if not matches:
                raise FileNotFoundError(f"No raw CSV for {symbol} on {self.date_str}")
            df = pd.read_csv(
                matches[0],
                parse_dates=["datetime"],
                index_col="datetime"
            )
            self.cache[symbol] = df
        return self.cache[symbol]

    def get_minute_slice(self, symbol: str, timestamp: dt.datetime) -> pd.Series:
        df = self.load_minute_bars(symbol)
        return df.loc[timestamp]

    def get_last_n_bars(self, symbol: str, timestamp: dt.datetime, n: int) -> pd.DataFrame:
        df = self.load_minute_bars(symbol)
        idx = df.index.get_loc(timestamp)
        start = max(0, idx - n + 1)
        return df.iloc[start : idx + 1]

    def get_prev_close(self, symbol: str, timestamp: dt.datetime) -> float:
        df = self.load_minute_bars(symbol)
        return float(df["prev_close"].iloc[0])

    # ——— IB-like API stubs ———

    def isConnected(self) -> bool:
        return True

    def reqAccountSummary(self, *args, **kwargs):
        # engines often call: reqAccountSummary("", "All", "$LEDGER")
        return []

    def set_current_time(self, timestamp: dt.datetime):
        """Call once per minute so reqRealTimeBars knows which row to serve."""
        self._current_ts = timestamp

    def reqHistoricalData(
            self, contract, endDateTime, durationStr,
            barSizeSetting, whatToShow, useRTH,
            formatDate, keepUpToDate, chartOptions
    ):
        # treat durationStr = "N whatever" → N bars
        try:
            n = int(durationStr.split()[0])
        except:
            n = 1
        df = self.get_last_n_bars(contract.symbol, endDateTime, n)
        bars = []
        for ts, row in df.iterrows():
            bars.append(BarData(
                time=ts,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume", 0),
            ))
        return bars

    def reqRealTimeBars(
            self, contract, barSize, whatToShow, useRTH, formatDate=1
    ):
        ts = self._current_ts
        if ts is None:
            raise RuntimeError("No current timestamp set on CSVDataProvider")
        row = self.get_minute_slice(contract.symbol, ts)
        return BarData(
            time=ts,
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row.get("volume", 0),
        )
