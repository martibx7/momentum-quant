"""Exit Engine — Stage‑4 (position management & exits)
====================================================
Applies blueprint §6 risk rules to all live positions:

* **Initial stop** at –1 R from entry.
* **Staircase**: when price hits +1 R, stop moves to break‑even; +2 R → stop
  locks +1 R; +3 R → stop follows 9‑EMA or first‑red close (whichever sooner).
* **First‑red‑close**: if enabled and price ≥ +2 R, dump
  `exit.first_red_exit_pct` % of remaining shares on the first red 1‑min bar.

Outputs
-------
* Updates stop‑loss orders via ``libs.broker_api``.
* Executes partial / full exits as market orders when rules fire.
* Logs to `alerts/exit_YYYYMMDD.csv` or publishes JSON to Redis.

Depends on ``libs.ledger`` (position book) and ``libs.broker_api``.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import math
import pathlib
from typing import Dict, Optional

import pandas as pd
import yaml
from ib_insync import IB, Stock  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis
except ModuleNotFoundError:
    redis = None  # type: ignore

from libs.broker_api import BrokerAPI
from libs.ledger import Ledger

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ───────────────────────── Config & paths ────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = yaml.safe_load((_ROOT / "config.yml").read_text())
EXIT_CFG = _CFG["exit"]
_EXIT_CSV = _ROOT / "alerts" / f"exit_{dt.date.today():%Y%m%d}.csv"
_ET = ZoneInfo("America/New_York")

if EXIT_CFG.get("publish", "csv") == "redis" and redis is None:
    raise RuntimeError("publish=redis requires redis‑py installed")

if EXIT_CFG.get("publish", "csv") == "csv" and not _EXIT_CSV.exists():
    with _EXIT_CSV.open("w", newline="") as f:
        csv.writer(f).writerow(["ts", "symbol", "action", "qty", "price", "comment"])

# ────────────────────── helper dataclass ───────────────────────────────
class PosState:
    def __init__(self, qty: int, entry: float, stop: float):
        self.qty = qty
        self.entry = entry
        self.stop = stop
        self.locked_R = 0  # 0,1,2…
        self.first_red_exited = False

# ────────────────────── Exit engine ────────────────────────────────────
class ExitEngine:
    """Monitors open positions and adjusts stops / exits."""

    def __init__(self, ib: Optional[IB] = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=20)
        self.broker = BrokerAPI(self.ib)
        self.ledger = Ledger()
        self._mode = EXIT_CFG.get("publish", "csv")
        self._redis = redis.Redis() if self._mode == "redis" else None  # type: ignore
        self._states: Dict[str, PosState] = {}

    # ─────────────────── main loop ─────────────────────────────
    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        self._refresh_positions()
        for sym, state in list(self._states.items()):
            bar_df = self._bars(sym)
            if bar_df is None:
                continue
            last = bar_df.close.iloc[-1]
            R_val = state.entry * (abs(EXIT_CFG["stop_R"]) / 100)  # –1R is 1% placeholder
            pnl_R = (last - state.entry) / R_val
            # Staircase logic
            if pnl_R >= 1 and state.locked_R < 0:
                self._move_stop(sym, state.entry)  # BE
                state.locked_R = 0
            if pnl_R >= 2 and state.locked_R < 1:
                self._move_stop(sym, state.entry + R_val)  # lock +1R
                state.locked_R = 1
            if pnl_R >= 3:
                self._trail_ema(sym, bar_df, state)
                self._first_red_exit(sym, bar_df, state)
            # Hard stop violation handled by broker; if position closed, ledger will refresh
        self._prune_closed()

    # ───────── refresh pos from ledger ─────────
    def _refresh_positions(self):
        live = self.ledger.live_positions()
        # Add new ones
        for sym, pos in live.items():
            if sym not in self._states:
                self._states[sym] = PosState(pos.qty, pos.entry_price, pos.stop_price)
        # Remove exited ones
        for sym in list(self._states):
            if sym not in live:
                self._states.pop(sym, None)

    # ────────── supporting funcs ─────────────
    def _bars(self, sym: str):
        try:
            bars = self.ib.reqHistoricalData(Stock(sym, "SMART", "USD"), "", "20 mins", "1 min", "TRADES", True, 1, False, [])
            if not bars:
                return None
            return pd.DataFrame([{"open": b.open, "close": b.close} for b in bars])
        except Exception as exc:
            _LOG.warning("bars fail %s: %s", sym, exc)
            return None

    def _move_stop(self, sym: str, new_stop: float):
        self.broker.move_stop_loss(sym, new_stop)
        self._log(sym, 0, new_stop, f"move_stop->{new_stop:.2f}")

    def _trail_ema(self, sym: str, df: pd.DataFrame, state: PosState):
        ema_len = EXIT_CFG["ema_trailing_len"]
        ema_val = df.close.ewm(span=ema_len).mean().iloc[-1]
        if ema_val > state.stop:
            state.stop = ema_val
            self._move_stop(sym, ema_val)

    def _first_red_exit(self, sym: str, df: pd.DataFrame, state: PosState):
        if state.first_red_exited:
            return
        if df.close.iloc[-1] < df.open.iloc[-1]:
            qty_exit = math.floor(state.qty * EXIT_CFG["first_red_exit_pct"] / 100)
            if qty_exit > 0:
                self.broker.market_sell(sym, qty_exit)
                self._log(sym, qty_exit, df.close.iloc[-1], "first_red")
                state.qty -= qty_exit
                state.first_red_exited = True

    def _prune_closed(self):
        for sym, st in list(self._states.items()):
            if st.qty <= 0:
                self._states.pop(sym, None)

    # ─────────── logging / publish ───────────
    def _log(self, sym, qty, price, comment):
        ts = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        if self._mode == "csv":
            with _EXIT_CSV.open("a", newline="") as f:
                csv.writer(f).writerow([ts.isoformat(timespec="seconds"), sym, "SELL", qty, f"{price:.2f}", comment])
        else:
            self._redis.publish(EXIT_CFG.get("redis_channel", "exit_events"), json.dumps({"ts": ts.isoformat(timespec="seconds"), "symbol": sym, "qty": qty, "price": price, "comment": comment}))  # type: ignore
        _LOG.info("EXIT %s %s @%.2f (%s)", sym, qty, price, comment)

# ─────────────────────────── CLI ─────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    eng = ExitEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            _LOG.exception(exc)
            eng.ib.sleep(30)
