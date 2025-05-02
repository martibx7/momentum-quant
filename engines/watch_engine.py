"""Watch Engine — Stage-1 (micro-pullback validation)
===================================================
Consumes raw *alerts* (Stage-0) and waits for each symbol to form an
acceptable micro-pullback before promoting it to **armed** status.

Uses IBKR’s real-time tick feed by default (no extra subscription needed),
falls back to historical 1-min bars only if you do have the data.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import pathlib
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List

import pandas as pd
import yaml
from ib_insync import IB, Stock, util  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis  # optional for live pub/sub
except ModuleNotFoundError:
    redis = None  # type: ignore

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ───────────────────────────── Config & Paths ────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = yaml.safe_load((_REPO_ROOT / "config.yml").read_text(encoding="utf-8"))
WATCH_CFG = _CFG["watch"]
_ALERT_DIR = _REPO_ROOT / "alerts"
_ARMED_CSV = _ALERT_DIR / f"armed_{dt.date.today():%Y%m%d}.csv"
_ET = ZoneInfo("America/New_York")

if WATCH_CFG.get("publish", "csv") == "redis" and redis is None:
    raise RuntimeError("publish=redis requires redis-py installed")

# ───────────────────────────── Data structures ───────────────────────────────
@dataclass
class PullbackState:
    symbol: str
    alert_ts: dt.datetime
    high_price: float
    bars: Deque[pd.Series] = field(default_factory=lambda: deque(maxlen=20))

    def update(self, bar: pd.Series):
        self.bars.append(bar)
        self.high_price = max(self.high_price, bar.close)

    def is_valid_pullback(self, cfg: dict) -> bool:
        if len(self.bars) < 3:
            return False
        # Only consider recent red bars up to limit
        red_bars = [b for b in reversed(self.bars) if b.close < b.open]
        red_count = red_vol = green_vol = 0
        for b in red_bars:
            if red_count >= cfg["max_red_bars"]:
                break
            red_count += 1
            red_vol += b.volume
        # match each red bar with a prior green bar for volume comparison
        greens_checked = 0
        for b in reversed(self.bars):
            if greens_checked >= red_count:
                break
            if b.close >= b.open:
                green_vol += b.volume
                greens_checked += 1
        if red_count == 0 or green_vol == 0:
            return False
        # pullback percent
        last_close = self.bars[-1].close
        pull_pct = (self.high_price - last_close) / self.high_price * 100
        if pull_pct > cfg["max_pullback_pct"]:
            return False
        # volume ratio
        if red_vol / green_vol > cfg["low_vol_ratio"]:
            return False
        # optional VWAP hold
        if cfg.get("must_hold_vwap", True):
            total_v = sum(b.volume for b in self.bars)
            vwap = sum(b.close * b.volume for b in self.bars) / total_v
            if last_close < vwap:
                return False
        return True

# ───────────────────────────── Watch Engine ──────────────────────────────────
class WatchEngine:
    """Consumes stage-0 alerts and emits armed-alerts when pullback criteria hit."""

    def __init__(self, ib: IB | None = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=18)

        self.states: Dict[str, PullbackState] = {}
        self.timeout = dt.timedelta(minutes=WATCH_CFG["timeout_minutes"])
        self.pb_cfg = WATCH_CFG["micro_pullback"]
        self._publish_mode = WATCH_CFG.get("publish", "csv")
        self._redis_cli = redis.Redis() if self._publish_mode == "redis" else None  # type: ignore

        if self._publish_mode == "csv" and not _ARMED_CSV.exists():
            _ALERT_DIR.mkdir(exist_ok=True)
            with _ARMED_CSV.open("w", newline="") as f:
                csv.writer(f).writerow(["ts", "symbol", "price"])

    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        self._purge_timeouts(now)

        new_alerts = self._consume_new_alert_rows()
        _LOG.info("WatchEngine: consumed %d new alerts", len(new_alerts))
        for al in new_alerts:
            self.states[al["symbol"]] = PullbackState(
                symbol=al["symbol"],
                alert_ts=dt.datetime.fromisoformat(al["ts"]).astimezone(_ET),
                high_price=float(al["price"]),
            )

        for sym, st in list(self.states.items()):
            bar = self._latest_bar(sym)
            if bar is None:
                _LOG.info("WatchEngine: no bar for %s this tick", sym)
                continue
            st.update(bar)
            _LOG.info("WatchEngine: %s → %d bars in state (high=%.2f)",
                      sym, len(st.bars), st.high_price)
            if st.is_valid_pullback(self.pb_cfg):
                _LOG.info("WatchEngine: %s pullback valid, emitting ARMED", sym)
                self._emit_armed(st, now)
                self.states.pop(sym, None)

    def _purge_timeouts(self, now: dt.datetime):
        for sym, st in list(self.states.items()):
            if now - st.alert_ts > self.timeout:
                _LOG.info("WatchEngine: timing out %s", sym)
                self.states.pop(sym, None)

    def _consume_new_alert_rows(self) -> List[dict]:
        path = _ALERT_DIR / f"alert_{dt.date.today():%Y%m%d}.csv"
        if not path.exists():
            return []
        if not hasattr(self, "_last_pos"):
            self._last_pos = 0  # type: ignore
        rows: List[dict] = []
        with path.open() as f:
            f.seek(self._last_pos)
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 3 or parts[0] == "ts":
                    continue
                rows.append({"ts": parts[0], "symbol": parts[1], "price": parts[2]})
            self._last_pos = f.tell()
        return rows

    def _latest_bar(self, symbol: str):
        """Try a real-time 60s bar; if none, fall back to a last-tick quote."""
        contract = Stock(symbol, "SMART", "USD")

        # 1) Real-time bars (no subscription needed for Level-1)
        try:
            rtbars = self.ib.reqRealTimeBars(contract, barSize=60, whatToShow="TRADES", useRTH=False)
            df = util.df(rtbars)
            if not df.empty:
                last = df.iloc[-1]
                return pd.Series({
                    "open":  last["open"],
                    "close": last["close"],
                    "volume": last["volume"],
                })
        except Exception:
            pass

        # 2) Fallback to a single tick
        try:
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(0.2)
            last_price = ticker.last or ticker.close
            self.ib.cancelMktData(ticker)
            if last_price is None:
                return None
            return pd.Series({"open": last_price, "close": last_price, "volume": 0})
        except Exception:
            return None

    def _emit_armed(self, st: PullbackState, now: dt.datetime):
        record = [now.isoformat(timespec="seconds"), st.symbol, f"{st.bars[-1].close:.2f}"]
        if self._publish_mode == "csv":
            with _ARMED_CSV.open("a", newline="") as f:
                csv.writer(f).writerow(record)
            _LOG.info("ARMED: %s", st.symbol)
        else:
            payload = json.dumps({"ts": record[0], "symbol": st.symbol, "price": st.bars[-1].close})
            self._redis_cli.publish(WATCH_CFG.get("redis_channel", "armed_alerts"), payload)
            _LOG.info("ARMED→redis: %s", st.symbol)

if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    eng = WatchEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception:
            _LOG.exception("Unexpected error in WatchEngine, pausing...")
            eng.ib.sleep(60)
