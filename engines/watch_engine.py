"""Watch Engine — Stage‑1 (micro‑pullback validation)
===================================================
Consumes raw *alerts* (Stage‑0) and waits for each symbol to form an
acceptable micro‑pullback before promoting it to **armed** status.

* Input  : alerts/alert_YYYYMMDD.csv   (or Redis stream)
* Output : alerts/armed_YYYYMMDD.csv   (or Redis channel)

Blueprint §3/§4 rules implemented:
---------------------------------
* Timeout                    – purge alert after `watch.timeout_minutes`.
* Pull‑back max red bars     – `watch.micro_pullback.max_red_bars`.
* Max pull‑back %            – `watch.micro_pullback.max_pullback_pct`.
* Must hold VWAP             – if enabled in config.
* Low‑volume requirement     – red‑bar vol ÷ green‑bar vol ≤ `low_vol_ratio`.

Dependencies:  ``ib_insync``, ``pandas``, ``pyyaml``.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import pathlib
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Sequence

import pandas as pd
import yaml
from ib_insync import IB, Stock  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis  # optional for live pub/sub
except ModuleNotFoundError:
    redis = None  # type: ignore

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ───────────────────────────── Config & Paths ────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = yaml.safe_load((_REPO_ROOT / "config.yml").read_text())
WATCH_CFG = _CFG["watch"]
SCN_CFG = _CFG["scanner"]  # need session windows / tz for VWAP pricing
_ALERT_DIR = _REPO_ROOT / "alerts"
_ARMED_CSV = _ALERT_DIR / f"armed_{dt.date.today():%Y%m%d}.csv"
_ET = ZoneInfo("America/New_York")

if WATCH_CFG.get("publish", "csv") == "redis" and redis is None:
    raise RuntimeError("publish=redis requires redis‑py installed")

# ───────────────────────────── Data structures ───────────────────────────────
@dataclass
class PullbackState:
    symbol: str
    alert_ts: dt.datetime
    high_price: float
    bars: Deque[pd.Series] = field(default_factory=lambda: deque(maxlen=20))  # last ~20 min

    def update(self, bar: pd.Series):
        self.bars.append(bar)
        self.high_price = max(self.high_price, bar.close)

    # ───────────────────────── Rule checks ────────────────────────────────
    def is_valid_pullback(self, cfg: dict) -> bool:
        if len(self.bars) < 3:
            return False
        # Only consider the most recent consecutive red bars up to max_red_bars
        red_bars = [b for b in reversed(self.bars) if b.close < b.open]
        red_count = 0
        red_vol_sum = 0
        green_vol_sum = 0
        for b in red_bars:
            if red_count >= cfg["max_red_bars"]:
                break
            red_count += 1
            red_vol_sum += b.volume
        # Collect preceding green bars up to same count
        greens_checked = 0
        for b in reversed(self.bars):
            if greens_checked >= red_count:
                break
            if b.close >= b.open:
                green_vol_sum += b.volume
                greens_checked += 1
        if red_count == 0:
            return False
        # Pullback %
        last_close = self.bars[-1].close
        pull_pct = (self.high_price - last_close) / self.high_price * 100
        if pull_pct > cfg["max_pullback_pct"]:
            return False
        # Volume ratio
        if green_vol_sum == 0:
            return False
        if red_vol_sum / green_vol_sum > cfg["low_vol_ratio"]:
            return False
        # VWAP hold (approx) – ensure last close >= session VWAP
        if cfg.get("must_hold_vwap", True):
            vwap = sum(b.close * b.volume for b in self.bars) / sum(b.volume for b in self.bars)
            if last_close < vwap:
                return False
        return True


# ───────────────────────────── Watch Engine ──────────────────────────────────
class WatchEngine:
    """Consumes stage‑0 alerts and emits armed‑alerts when pullback criteria hit."""

    def __init__(self, ib: IB | None = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=18)

        self.states: Dict[str, PullbackState] = {}
        self.timeout = dt.timedelta(minutes=WATCH_CFG["timeout_minutes"])
        self.pb_cfg = WATCH_CFG["micro_pullback"]
        # Live publish mode
        self._publish_mode = WATCH_CFG.get("publish", "csv")
        self._redis_cli = redis.Redis() if self._publish_mode == "redis" else None  # type: ignore
        if self._publish_mode == "csv" and not _ARMED_CSV.exists():
            with _ARMED_CSV.open("w", newline="") as f:
                csv.writer(f).writerow([
                    "ts", "symbol", "price", "rv", "pctGain", "float", "spreadPct", "trend", "qs"
                ])

    # ───────────────────────── Main loop tick ───────────────────────────────
    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        self._purge_timeouts(now)
        new_alerts = self._consume_new_alert_rows()
        for al in new_alerts:
            self.states[al["symbol"]] = PullbackState(
                symbol=al["symbol"],
                alert_ts=dt.datetime.fromisoformat(al["ts"]).astimezone(_ET),
                high_price=float(al["price"]),
            )
        # Update each tracked symbol with latest bar & test pullback
        for sym, st in list(self.states.items()):
            bar = self._latest_bar(sym)
            if bar is None:
                continue
            st.update(bar)
            if st.is_valid_pullback(self.pb_cfg):
                self._emit_armed(st, now)
                self.states.pop(sym, None)

    # ──────────────────────── Helpers ───────────────────────────────────────
    def _purge_timeouts(self, now: dt.datetime):
        for sym, st in list(self.states.items()):
            if now - st.alert_ts > self.timeout:
                self.states.pop(sym, None)

    def _consume_new_alert_rows(self) -> List[dict]:
        """Read new rows appended to today's alert CSV since last tick."""
        path = _ALERT_DIR / f"alert_{dt.date.today():%Y%m%d}.csv"
        if not path.exists():
            return []
        if not hasattr(self, "_last_pos"):
            self._last_pos = 0  # type: ignore
        rows = []
        with path.open() as f:
            f.seek(self._last_pos)
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 9 or parts[0] == "ts":
                    continue
                rows.append({
                    "ts": parts[0],
                    "symbol": parts[1],
                    "price": parts[2],
                    "rv": parts[3],
                    "pctGain": parts[4],
                    "float": parts[5],
                    "spreadPct": parts[6],
                    "trend": parts[7],
                    "qs": parts[8],
                })
            self._last_pos = f.tell()
        return rows

    def _latest_bar(self, symbol: str):
        try:
            bars = self.ib.reqHistoricalData(Stock(symbol, "SMART", "USD"), "", "3 mins", "1 min", "TRADES", True, 1, False, [])
            if not bars:
                return None
            b = bars[-1]
            return pd.Series({"open": b.open, "close": b.close, "volume": b.volume})
        except Exception as exc:  # pylint:disable=broad-except
            _LOG.warning("bar fetch fail %s: %s", symbol, exc)
            return None

    def _emit_armed(self, st: PullbackState, now: dt.datetime):
        record = [
            now.isoformat(timespec="seconds"), st.symbol, f"{st.bars[-1].close:.2f}",
        ]
        # Remaining meta we can’t track easily here → placeholders → to be enriched later
        if self._publish_mode == "csv":
            with _ARMED_CSV.open("a", newline="") as f:
                csv.writer(f).writerow(record)
            _LOG.info("ARMED: %s", st.symbol)
        else:  # redis
            payload = json.dumps({"ts": record[0], "symbol": st.symbol, "price": st.bars[-1].close})
            self._redis_cli.publish(WATCH_CFG.get("redis_channel", "armed_alerts"), payload)  # type: ignore
            _LOG.info("ARMED→redis: %s", st.symbol)

# ─────────────────────────── CLI convenience ────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    eng = WatchEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception as exc:  # pylint:disable=broad-except
            _LOG.exception(exc)
            eng.ib.sleep(60)
