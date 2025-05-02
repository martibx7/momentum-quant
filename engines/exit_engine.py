"""Exit Engine — Stage-4 (position management & exits)
====================================================
Applies blueprint §6 risk rules to all live positions using IBKR’s
free real-time 1 min bars where possible, with historical fallback.
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
from ib_insync import IB, Stock, util  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis
except ModuleNotFoundError:
    redis = None  # type: ignore

from libs.broker_api import BrokerAPI
from libs.ledger import Ledger

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ───────────────────────── Config & Paths ────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parents[1]
with open(_ROOT / "config.yml", encoding="utf-8") as f:
    _CFG = yaml.safe_load(f)
EXIT_CFG = _CFG["exit"]
_EXIT_CSV = _ROOT / "alerts" / f"exit_{dt.date.today():%Y%m%d}.csv"
_ET = ZoneInfo("America/New_York")

if EXIT_CFG.get("publish") == "redis" and redis is None:
    raise RuntimeError("publish=redis requires redis-py installed")

# ensure header
if EXIT_CFG.get("publish") == "csv" and not _EXIT_CSV.exists():
    with _EXIT_CSV.open("w", newline="") as f:
        csv.writer(f).writerow(["ts", "symbol", "action", "qty", "price", "comment"])

class PosState:
    def __init__(self, qty: int, entry: float, stop: float):
        self.qty = qty
        self.entry = entry
        self.stop = stop
        self.locked_R = -1  # start at initial stop = –1R
        self.first_red_exited = False

class ExitEngine:
    def __init__(self, ib: Optional[IB] = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=20)
        self.broker = BrokerAPI(self.ib)
        self.ledger = Ledger(self.broker)
        self._mode = EXIT_CFG.get("publish")
        self._redis = redis.Redis() if self._mode == "redis" else None
        self._states: Dict[str, PosState] = {}

    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        self._refresh_positions()
        for sym, state in list(self._states.items()):
            df = self._bars(sym)
            if df is None or df.empty:
                continue

            last = df.close.iloc[-1]
            R_val = state.entry * abs(EXIT_CFG["stop_R"]) / 100.0
            pnl_R = (last - state.entry) / R_val

            # Stage stops:
            if pnl_R >= 1 and state.locked_R < 0:
                # +1R hit → move stop to BE
                self._move_stop(sym, state.entry)
                state.locked_R = 0

            if pnl_R >= 2 and state.locked_R < 1:
                # +2R hit → lock +1R
                self._move_stop(sym, state.entry + R_val)
                state.locked_R = 1

            if pnl_R >= 3:
                # +3R hit → start trailing
                self._trail_ema(sym, df, state)
                # and first-red-close exit
                self._first_red_exit(sym, df, state)

        self._prune_closed()

    def _refresh_positions(self):
        live = self.ledger.live_positions()
        # add new positions
        for sym, pos in live.items():
            if sym not in self._states:
                self._states[sym] = PosState(pos.qty, pos.entry_price, pos.stop_price)
        # remove closed
        for sym in list(self._states):
            if sym not in live:
                self._states.pop(sym)

    def _bars(self, sym: str) -> Optional[pd.DataFrame]:
        """1) Try real-time bars; 2) fallback to historical 1-min bars."""
        contract = Stock(sym, "SMART", "USD")
        # real-time
        try:
            rtb = self.ib.reqRealTimeBars(contract, barSize=60, whatToShow="TRADES", useRTH=False)
            df = util.df(rtb)
            if not df.empty:
                return df.iloc[-20:][["open", "close"]].reset_index(drop=True)
        except Exception:
            pass
        # historical fallback
        try:
            bars = self.ib.reqHistoricalData(
                contract, "", "20 mins", "1 min",
                "TRADES", True, 1, False, []
            )
            return pd.DataFrame([{"open": b.open, "close": b.close} for b in bars])
        except Exception as e:
            _LOG.warning("bars fail %s: %s", sym, e)
            return None

    def _move_stop(self, sym: str, new_stop: float):
        self.broker.move_stop_loss(sym, new_stop)
        self._log(sym, 0, new_stop, f"move_stop→{new_stop:.2f}")

    def _trail_ema(self, sym: str, df: pd.DataFrame, state: PosState):
        ema_len = EXIT_CFG["ema_trailing_len"]
        ema_val = df.close.ewm(span=ema_len).mean().iloc[-1]
        if ema_val > state.stop:
            state.stop = ema_val
            self._move_stop(sym, ema_val)

    def _first_red_exit(self, sym: str, df: pd.DataFrame, state: PosState):
        if state.first_red_exited:
            return
        # first red close after +3R
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
                self._states.pop(sym)

    def _log(self, sym: str, qty: int, price: float, comment: str):
        ts = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        if self._mode == "csv":
            with _EXIT_CSV.open("a", newline="") as f:
                csv.writer(f).writerow([
                    ts.isoformat(timespec="seconds"),
                    sym, "SELL", qty, f"{price:.2f}", comment
                ])
        else:
            self._redis.publish(
                EXIT_CFG.get("redis_channel", "exit_events"),
                json.dumps({
                    "ts": ts.isoformat(timespec="seconds"),
                    "symbol": sym,
                    "action": "SELL",
                    "qty": qty,
                    "price": price,
                    "comment": comment
                })
            )
        _LOG.info("EXIT %s %d @ %.2f (%s)", sym, qty, price, comment)


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    eng = ExitEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception:
            _LOG.exception("Unexpected error in ExitEngine, pausing…")
            eng.ib.sleep(30)
