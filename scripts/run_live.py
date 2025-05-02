"""run_live.py — live supervisor for Momentum-Quant
==================================================
Launches all four engines in a single cooperative loop (single-threaded) so
you can start paper trading instantly from one terminal.
"""
from __future__ import annotations

import logging
import csv
import datetime as dt
import pathlib
from dataclasses import dataclass
from typing import List, Sequence

import pandas as pd
import yaml
import yfinance as yf
from ib_insync import IB, Stock, ScannerSubscription  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis        # optional pub mode
except ModuleNotFoundError:
    redis = None        # type: ignore

# ────────────────────────── Logging setup ────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO
)
logging.getLogger('ib_insync').setLevel(logging.WARNING)

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ────────────────────────── Paths & constants ───────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONFIG    = yaml.safe_load((_REPO_ROOT / "config.yml").read_text(encoding="utf-8"))
_ALERT_DIR = _REPO_ROOT / "alerts"
_ALERT_DIR.mkdir(exist_ok=True)
_ET        = ZoneInfo("America/New_York")

SCAN_CFG = _CONFIG["scanner"]

# ───────────────────────────── Data class ────────────────────────────────────
@dataclass(slots=True)
class Alert:
    ts: dt.datetime
    symbol: str
    price: float
    rv: float
    pct_gain: float
    float_sh: int
    spread_pct: float
    trend: float

    @property
    def quality(self) -> float:
        return self.rv * self.trend

    def as_csv(self) -> List[str]:
        return [
            self.ts.isoformat(timespec="seconds"),
            self.symbol,
            f"{self.price:.2f}",
            f"{self.rv:.2f}",
            f"{self.pct_gain:.2f}",
            str(self.float_sh),
            f"{self.spread_pct:.2f}",
            f"{self.trend:.2f}",
            f"{self.quality:.2f}",
        ]

# ───────────────────────── Scanner Engine ──────────────────────────────────
class ScannerEngine:
    """Stage-0 scanner that emits alerts every minute."""

    def __init__(self, ib: IB | None = None):
        self.ib = ib or IB()
        # clear default handlers & install our filter
        self.ib.errorEvent.clear()
        self.ib.errorEvent += self._on_error

        if not self.ib.isConnected():
            self.ib.connect(
                "127.0.0.1", 7497,
                clientId=SCAN_CFG.get("client_ids", {}).get("scanner", 17)
            )

        if SCAN_CFG.get("publish") == "redis":
            if redis is None:
                raise RuntimeError("publish=redis requires redis-py installed")
            self.redis_cli = redis.Redis()
        else:
            self.redis_cli = None  # type: ignore

        _LOG.info("ScannerEngine initialized, IB connection ready.")

    def _on_error(self, reqId, errorCode, errorString, contract):
        # swallow 162 & farm-OK warnings
        if errorCode in (162, 2104, 2106, 2158):
            return
        _LOG.error("IB error %s: %s", errorCode, errorString)

    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        if not self._in_session(now.time()):
            return

        bars = self._fetch_bars()
        if bars.empty:
            _LOG.info("No symbols with sufficient bars this cycle.")
            return

        alerts = self._build_alerts(bars, now)
        if alerts:
            self._publish(alerts)
        else:
            _LOG.info("No alerts this cycle.")

    def _fetch_bars(self) -> pd.DataFrame:
        sub = ScannerSubscription(
            instrument='STK',
            locationCode='STK.US.MAJOR',
            scanCode='TOP_PERC_GAIN',
            abovePrice=SCAN_CFG["min_price"],
            belowPrice=SCAN_CFG["max_price"],
            aboveVolume=SCAN_CFG["min_volume"],
            numberOfRows=SCAN_CFG.get("number_of_rows", 50)
        )

        data_list = self.ib.reqScannerData(sub, [])
        self.ib.sleep(2.0)
        symbols = [r.contractDetails.contract.symbol for r in data_list]
        _LOG.info("Scanner returned %d symbols: %s", len(symbols), symbols)

        rows = []
        for sym in symbols:
            c = Stock(sym, "SMART", "USD")
            # **full-session** 1-min bars
            bars = self.ib.reqHistoricalData(
                c, "", "1 D", "1 min", "TRADES", True, 1, False, []
            )
            self.ib.sleep(0.25)
            if len(bars) < 21:
                _LOG.info("Insufficient bars for %s: %d", sym, len(bars))
                continue

            last = bars[-1]
            avg20 = sum(b.volume for b in bars[-21:-1]) / 20
            # daily bars for prev close
            dbar = self.ib.reqHistoricalData(
                c, "", "2 D", "1 day", "TRADES", True, 1, False, []
            )
            prev_close = dbar[-2].close if len(dbar) >= 2 else None
            rows.append((sym, last.close, last.volume, avg20, prev_close))

        df = pd.DataFrame(rows, columns=[
            "symbol", "close", "volume", "avgVol20", "prevClose"
        ])
        _LOG.info("Symbols passing bar check: %d", len(df))
        return df

    def _build_alerts(self, df: pd.DataFrame, now: dt.datetime):
        rv_thr = (SCAN_CFG["pre_open_rv"]
                  if now.time() <= dt.time(9, 45)
                  else SCAN_CFG["intraday_rv"])
        pct_min = SCAN_CFG.get("pct_gainer_min", 0)
        alerts: List[Alert] = []
        for row in df.itertuples(index=False):
            pct_gain = (row.close - (row.prevClose or row.close)) \
                       / (row.prevClose or row.close) * 100
            if pct_gain < pct_min:
                _LOG.info("Dropped %s: pct_gain %.1f%% < %.1f%%",
                          row.symbol, pct_gain, pct_min)
                continue

            rv = row.volume / max(row.avgVol20, 1)
            if rv < rv_thr:
                _LOG.info("Dropped %s: rv %.2f < %.2f",
                          row.symbol, rv, rv_thr)
                continue

            fs = self._float(row.symbol)
            if fs and fs > SCAN_CFG["float_max"]:
                _LOG.info("Dropped %s: float %d > %d",
                          row.symbol, fs, SCAN_CFG["float_max"])
                continue

            sp = self._spread(row.symbol, row.close)
            if sp > SCAN_CFG["spread_max_pct"]:
                _LOG.info("Dropped %s: spread %.2f%% > %.2f%%",
                          row.symbol, sp, SCAN_CFG["spread_max_pct"])
                continue

            trend = self._trend(row.symbol)
            alerts.append(Alert(now, row.symbol, row.close,
                                rv, pct_gain, fs, sp, trend))
        return alerts

    def _publish(self, alerts: Sequence[Alert]):
        path = _ALERT_DIR / f"alert_{dt.date.today():%Y%m%d}.csv"
        new_file = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow([
                    "ts","symbol","price","rv",
                    "pctGain","float","spreadPct",
                    "trend","qs"
                ])
            for a in alerts:
                w.writerow(a.as_csv())
        _LOG.info("%d alerts → %s", len(alerts), path.name)

    def _in_session(self, t: dt.time) -> bool:
        for win in SCAN_CFG["session_windows"]:
            sh, sm = map(int, win["start"].split(":"))
            eh, em = map(int, win["end"].split(":"))
            if dt.time(sh, sm) <= t <= dt.time(eh, em):
                return True
        return False

    def _float(self, sym: str) -> int:
        try:
            return int(yf.Ticker(sym).info.get("floatShares") or 0)
        except Exception as exc:
            _LOG.warning("float fetch fail %s: %s", sym, exc)
            return 0

    def _spread(self, sym: str, last: float) -> float:
        q = self.ib.reqMktData(
            Stock(sym, "SMART", "USD"),
            "233", snapshot=True,
            regulatorySnapshot=False
        )
        self.ib.sleep(0.4)
        if not q.bid or not q.ask:
            return 99.0
        return (q.ask - q.bid) / last * 100

    def _trend(self, sym: str) -> float:
        bars = self.ib.reqHistoricalData(
            Stock(sym, "SMART", "USD"),
            "", "15 mins", "1 min",
            "TRADES", True, 1, False, []
        )
        closes = pd.Series([b.close for b in bars])
        if len(closes) < 3:
            return 0.0
        ema3 = closes.ewm(span=3).mean()
        slope = (ema3.iloc[-1] - ema3.iloc[-3]) / max(ema3.iloc[-3], 1e-4)
        return max(min(slope * 100, 1.5), 0)

# ──────────────────────────── CLI entry ─────────────────────────────────────
if __name__ == "__main__":
    _LOG.info("Starting ScannerEngine…")
    eng = ScannerEngine()
    _LOG.info("Entering run loop. Press Ctrl+C to stop.")

    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            _LOG.info("Keyboard interrupt received, shutting down.")
            break
        except Exception as exc:
            _LOG.exception("Main loop error: %s", exc)
            eng.ib.sleep(60)
