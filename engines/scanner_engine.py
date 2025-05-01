"""Scanner Engine — Stage‑0 (alert) | Momentum‑Quant
=================================================
Production‑ready module that discovers high‑RV momentum stocks and publishes
alerts for downstream engines. Now honours the **expanded config.yml** keys:

``scanner`` section
-------------------
- `session_windows`      trading windows (ET)
- `pre_open_rv` / `intraday_rv`
- `pct_gainer_min`       **NEW** minimum %‑up vs. prev close
- `min_price`, `max_price`, `min_volume` filters **NEW**
- `float_max`, `spread_max_pct`, `exclusion`
- `publish`, `redis_channel`

All other logic unchanged. Uses zoneinfo so DST is automatic.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import pathlib
from dataclasses import dataclass
from typing import List, Sequence

import pandas as pd
import yaml
import yfinance as yf
from ib_insync import IB, Stock, util  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis  # optional pub mode
except ModuleNotFoundError:
    redis = None  # type: ignore

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ────────────────────────── Paths & constants ────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]  # engines → project root
_CONFIG = yaml.safe_load((_REPO_ROOT / "config.yml").read_text())
_ALERT_DIR = _REPO_ROOT / "alerts"
_ALERT_DIR.mkdir(exist_ok=True)
_ET = ZoneInfo("America/New_York")

SCAN_CFG = _CONFIG["scanner"]

# ───────────────────────────── Data class ─────────────────────────────────────
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

    def as_json(self) -> str:
        return json.dumps({
            "ts": self.ts.isoformat(timespec="seconds"),
            "symbol": self.symbol,
            "price": self.price,
            "rv": self.rv,
            "pct_gain": self.pct_gain,
            "float": self.float_sh,
            "spread_pct": self.spread_pct,
            "trend": self.trend,
            "quality": self.quality,
        })


# ─────────────────────────── Scanner Engine ──────────────────────────────────
class ScannerEngine:
    """Stage‑0 scanner that emits alerts every minute."""

    def __init__(self, ib: IB | None = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=17)

        if SCAN_CFG["publish"] == "redis":
            if redis is None:
                raise RuntimeError("publish=redis requires redis‑py installed")
            self.redis_cli = redis.Redis()
        else:
            self.redis_cli = None  # type: ignore

    # ───────────────────────── Public loop ───────────────────────────────────
    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        if not self._in_session(now.time()):
            return
        bars = self._fetch_bars()
        if bars.empty:
            return
        alerts = self._build_alerts(bars, now)
        if alerts:
            self._publish(alerts)

    # ─────────────────────── Fetch universe minute bars ──────────────────────
    def _fetch_bars(self) -> pd.DataFrame:
        """IB scanner → pull last 1‑min bar + 20‑bar avg vol for each symbol."""
        min_price = SCAN_CFG["min_price"]
        max_price = SCAN_CFG["max_price"]
        min_vol = SCAN_CFG["min_volume"]

        scan_sub = self.ib.reqScannerSubscription(
            instrument="STK",
            locationCode="STK.US.MAJOR",
            scanCode="TOP_PERC_GAIN",
            abovePrice=min_price,
            belowPrice=max_price,
            aboveVolume=min_vol,
            numberOfRows=100,
        )
        scan_rows = self.ib.reqScannerData(scan_sub, []).wait(timeout=5)
        symbols = [r.contract.symbol for r in scan_rows]
        if not symbols:
            return pd.DataFrame()

        rows = []
        for sym in symbols:
            c = Stock(sym, "SMART", "USD")
            bars = self.ib.reqHistoricalData(c, "", "1800 S", "1 min", "TRADES", True, 1, False, [])
            if len(bars) < 21:
                continue
            last = bars[-1]
            avg20 = sum(b.volume for b in bars[-21:-1]) / 20
            # get prev close for pct‑gain
            dbar = self.ib.reqHistoricalData(c, "", "2 D", "1 day", "TRADES", True, 1, False, [])
            prev_close = dbar[-2].close if len(dbar) >= 2 else None
            rows.append((sym, last.close, last.volume, avg20, prev_close))

        return pd.DataFrame(rows, columns=["symbol", "close", "volume", "avgVol20", "prevClose"])

    # ───────────────────────── Build Alert list ──────────────────────────────
    def _build_alerts(self, df: pd.DataFrame, now: dt.datetime):
        rv_thr = SCAN_CFG["pre_open_rv"] if now.time() <= dt.time(9, 45) else SCAN_CFG["intraday_rv"]
        pct_min = SCAN_CFG.get("pct_gainer_min", 0)
        alerts: List[Alert] = []

        for row in df.itertuples(index=False):
            pct_gain = (row.close - (row.prevClose or row.close)) / (row.prevClose or row.close) * 100
            if pct_gain < pct_min:
                continue
            rv = row.volume / max(row.avgVol20, 1)
            if rv < rv_thr:
                continue
            float_sh = self._float(row.symbol)
            if float_sh and float_sh > SCAN_CFG["float_max"]:
                continue
            spread_pct = self._spread(row.symbol, row.close)
            if spread_pct > SCAN_CFG["spread_max_pct"]:
                continue
            trend = self._trend(row.symbol)
            alerts.append(Alert(now, row.symbol, row.close, rv, pct_gain, float_sh, spread_pct, trend))
        return alerts

    # ───────────────────────── Publish helpers ───────────────────────────────
    def _publish(self, alerts: Sequence[Alert]):
        if SCAN_CFG["publish"] == "csv":
            path = _ALERT_DIR / f"alert_{dt.date.today():%Y%m%d}.csv"
            new_file = not path.exists()
            with path.open("a", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["ts", "symbol", "price", "rv", "pctGain", "float", "spreadPct", "trend", "qs"])
                for a in alerts:
                    w.writerow(a.as_csv())
            _LOG.info("%d alerts → %s", len(alerts), path.name)
        else:  # redis
            for a in alerts:
                self.redis_cli.publish(SCAN_CFG.get("redis_channel", "scanner_alerts"), a.as_json())
            _LOG.info("%d alerts published to redis", len(alerts))

    # ───────────────────────── Misc helpers ──────────────────────────────────
    def _in_session(self, t: dt.time) -> bool:
        for win in SCAN_CFG["session_windows"]:
            start_h, start_m = map(int, win["start"].split(":"))
            end_h, end_m = map(int, win["end"].split(":"))
            if dt.time(start_h, start_m) <= t <= dt.time(end_h, end_m):
                return True
        return False

    def _float(self, sym: str) -> int:
        try:
            return int(yf.Ticker(sym).info.get("floatShares") or 0)
        except Exception as exc:  # pylint:disable=broad-except
            _LOG.warning("float fetch fail %s: %s", sym, exc)
            return 0

    def _spread(self, sym: str, last: float) -> float:
        q = self.ib.reqMktData(Stock(sym, "SMART", "USD"), "233", snapshot=True, regulatorySnapshot=False)
        self.ib.sleep(0.4)
        if not q.bid or not q.ask:
            return 99.0
        return (q.ask - q.bid) / last * 100

    def _trend(self, sym: str) -> float:
        bars = self.ib.reqHistoricalData(Stock(sym, "SMART", "USD"), "", "15 mins", "1 min", "TRADES", True, 1, False, [])
        closes = pd.Series([b.close for b in bars])
        if len(closes) < 3:
            return 0.0
        ema3 = closes.ewm(span=3).mean()
        slope = (ema3.iloc[-1] - ema3.iloc[-3]) / max(ema3.iloc[-3], 1e-4)
        return max(min(slope * 100, 1.5), 0)

# ──────────────────────────── CLI entry ──────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    eng = ScannerEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception as exc:  # pylint:disable=broad-except
            _LOG.exception(exc)
            eng.ib.sleep(60)
