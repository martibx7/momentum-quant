"""Entry Engine — Stage-2/3 (armed ➜ entry)
==========================================
Consumes armed alerts and converts them into real orders when trigger
conditions are satisfied.

Now uses IBKR’s free real-time 1 min bars by default, falling back to
historical data only if you have it.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import logging
import pathlib
from dataclasses import dataclass
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

# ────────────────────────── config / paths ────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
with open(_REPO_ROOT / "config.yml", encoding="utf-8") as f:
    _CFG = yaml.safe_load(f)
ENTRY_CFG = _CFG["entry"]

_ARMED_CSV = _REPO_ROOT / "alerts" / f"armed_{dt.date.today():%Y%m%d}.csv"
_ENTRY_CSV = _REPO_ROOT / "alerts" / f"entry_{dt.date.today():%Y%m%d}.csv"
_ET = ZoneInfo("America/New_York")

if ENTRY_CFG.get("publish") == "redis" and redis is None:
    raise RuntimeError("publish=redis requires redis-py installed")

# Ensure CSV header
if ENTRY_CFG.get("publish") == "csv" and not _ENTRY_CSV.exists():
    with _ENTRY_CSV.open("w", newline="") as f:
        csv.writer(f).writerow([
            "ts", "symbol", "side", "qty", "avgFill", "riskR", "comment"
        ])

@dataclass
class ArmedAlert:
    symbol: str
    high_ref: float
    ts: dt.datetime

class EntryEngine:
    def __init__(self, ib: Optional[IB] = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=19)
        self.broker = BrokerAPI(self.ib)
        self.ledger = Ledger()
        self._publish_mode = ENTRY_CFG.get("publish")
        self._redis_cli = redis.Redis() if self._publish_mode == "redis" else None
        self._armed: Dict[str, ArmedAlert] = {}
        self._armed_pos = 0

    def run_once(self):
        self._consume_new_armed()
        now_et = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        for sym, ar in list(self._armed.items()):
            df = self._latest_minute_bars(sym)
            if df is None or len(df) < 7:
                continue
            if not self._macd_trigger(df):
                continue
            if not self._vol_spike(df):
                continue
            spread = self._spread_pct(sym, df.close.iloc[-1])
            if spread > ENTRY_CFG["max_spread_pct"]:
                continue
            qty, risk_R = self._position_size(sym, df.close.iloc[-1])
            if qty <= 0:
                continue

            # first fill
            first_qty = math.ceil(qty * ENTRY_CFG["first_fill_pct"])
            order_id = self.broker.market_buy(sym, first_qty)
            self._log_order(now_et, sym, first_qty, risk_R, "first_fill", order_id)

            # optional break-add
            add_pct = ENTRY_CFG["add_after_break_pct"]
            self._armed.pop(sym, None)
            if add_pct > 0:
                target = ar.high_ref * (1 + add_pct/100)
                self.broker.register_break_add(sym, target, qty - first_qty)

    def _consume_new_armed(self):
        if not _ARMED_CSV.exists():
            return
        with _ARMED_CSV.open() as f:
            f.seek(self._armed_pos)
            for line in f:
                ts, sym, price, *rest = line.strip().split(",")
                if ts == "ts":
                    continue
                t = dt.datetime.fromisoformat(ts).astimezone(_ET)
                self._armed[sym] = ArmedAlert(sym, float(price), t)
            self._armed_pos = f.tell()

    def _latest_minute_bars(self, sym: str) -> Optional[pd.DataFrame]:
        """1) Try free real-time 1m bars; 2) fall back to historical if needed."""
        contract = Stock(sym, "SMART", "USD")
        # 1) real-time bars
        try:
            rtb = self.ib.reqRealTimeBars(
                contract, barSize=60, whatToShow="TRADES", useRTH=False
            )
            df = util.df(rtb)
            if not df.empty:
                # keep only last 10
                last10 = df.iloc[-10:][["open", "close", "volume"]]
                return last10.reset_index(drop=True)
        except Exception:
            pass

        # 2) fallback historical
        try:
            bars = self.ib.reqHistoricalData(
                contract, "", "10 mins", "1 min",
                "TRADES", True, 1, False, []
            )
            if bars:
                data = [
                    {"open": b.open, "close": b.close, "volume": b.volume}
                    for b in bars
                ]
                return pd.DataFrame(data)
        except Exception:
            pass

        return None

    def _macd_trigger(self, df: pd.DataFrame) -> bool:
        f, s, sig = ENTRY_CFG["macd_fast"], ENTRY_CFG["macd_slow"], ENTRY_CFG["macd_signal"]
        ema_f = df.close.ewm(span=f).mean()
        ema_s = df.close.ewm(span=s).mean()
        macd = ema_f - ema_s
        signal = macd.ewm(span=sig).mean()
        return macd.iloc[-2] < signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]

    def _vol_spike(self, df: pd.DataFrame) -> bool:
        avg5 = df.volume.iloc[-6:-1].mean()
        return df.volume.iloc[-1] >= avg5 * ENTRY_CFG["vol_spike_ratio"]

    def _spread_pct(self, sym: str, last: float) -> float:
        q = self.ib.reqMktData(Stock(sym, "SMART", "USD"), "233", snapshot=True)
        self.ib.sleep(0.3)
        if not q.bid or not q.ask:
            return 99.0
        return (q.ask - q.bid) / last * 100

    def _position_size(self, sym: str, price: float) -> tuple[int,float]:
        R_dollar = self.ledger.risk_unit()
        # TODO swap in your ATR-based stop distance
        stop_dist = price * 0.01
        qty = math.floor(R_dollar / stop_dist)
        if qty <= 0 or not self.ledger.ok_to_trade(sym, price, qty, stop_dist):
            return 0, 0.0
        return qty, (qty * stop_dist) / R_dollar

    def _log_order(self, ts, sym, qty, risk_R, comment, order_id):
        if self._publish_mode == "csv":
            with _ENTRY_CSV.open("a", newline="") as f:
                csv.writer(f).writerow([
                    ts.isoformat(timespec="seconds"),
                    sym, "BUY", qty, "pending", f"{risk_R:.2f}", comment
                ])
        else:
            self._redis_cli.publish(
                ENTRY_CFG.get("redis_channel", "entry_orders"),
                json.dumps({
                    "ts": ts.isoformat(timespec="seconds"),
                    "symbol": sym,
                    "qty": qty,
                    "riskR": risk_R,
                    "comment": comment,
                    "id": order_id
                })
            )
        _LOG.info("ORDER %s x%d (%s) → %s", sym, qty, comment, order_id)

if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO
    )
    eng = EntryEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception:
            _LOG.exception("Unexpected error in EntryEngine, pausing…")
            eng.ib.sleep(60)
