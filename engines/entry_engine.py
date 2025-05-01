"""Entry Engine — Stage‑2/3 (armed ➜ entry)
==========================================
Consumes armed alerts and converts them into real orders when trigger
conditions are satisfied.

Blueprint §5 rules implemented
------------------------------
* MACD 6‑13‑4 on 1‑min bars must cross signal ↑.
* Current 1‑min bar volume ≥ `entry.vol_spike_ratio` × avg( last 5 bars ).
* Bid‑ask spread ≤ `entry.max_spread_pct` (double‑check).
* First fill = `entry.first_fill_pct` of intended size; optional add after
  `entry.add_after_break_pct` move above pullback high.
* Sizing / risk determined via `libs.ledger.Ledger` (1 R = risk% × equity).

Outputs
-------
* Sends order via ``libs.broker_api`` (wrapper around ib_insync).
* Logs every attempted / filled trade to `alerts/entry_YYYYMMDD.csv`.
* Publishes JSON to Redis channel `entry_orders` if `entry.publish = redis`.

Depends on: ``ib_insync``, project ``libs`` (broker_api, ledger).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import math
import pathlib
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd
import yaml
from ib_insync import IB, Stock  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover
    redis = None  # type: ignore

from libs.broker_api import BrokerAPI  # thin wrapper
from libs.ledger import Ledger        # risk & cash tracking

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ────────────────────────── config / paths ────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = yaml.safe_load((_REPO_ROOT / "config.yml").read_text())
ENTRY_CFG = _CFG["entry"]
_ARMED_CSV = _REPO_ROOT / "alerts" / f"armed_{dt.date.today():%Y%m%d}.csv"
_ENTRY_CSV = _REPO_ROOT / "alerts" / f"entry_{dt.date.today():%Y%m%d}.csv"
_ET = ZoneInfo("America/New_York")

if ENTRY_CFG.get("publish", "csv") == "redis" and redis is None:
    raise RuntimeError("publish=redis requires redis‑py")

# header for CSV log
if ENTRY_CFG.get("publish", "csv") == "csv" and not _ENTRY_CSV.exists():
    with _ENTRY_CSV.open("w", newline="") as f:
        csv.writer(f).writerow([
            "ts", "symbol", "side", "qty", "avgFill", "riskR", "comment"
        ])

# ─────────────────────────── helpers ──────────────────────────────────────
@dataclass
class ArmedAlert:
    symbol: str
    high_ref: float           # pullback high
    ts: dt.datetime


# ────────────────────────── Entry Engine ───────────────────────────────────
class EntryEngine:
    """Listens for armed symbols and fires orders when triggers hit."""

    def __init__(self, ib: Optional[IB] = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=19)
        self.broker = BrokerAPI(self.ib)
        self.ledger = Ledger()
        self._publish_mode = ENTRY_CFG.get("publish", "csv")
        self._redis_cli = redis.Redis() if self._publish_mode == "redis" else None  # type: ignore
        self._armed: Dict[str, ArmedAlert] = {}
        # track last read position in armed CSV
        self._armed_pos = 0

    # ───────────────── loop ─────────────────────────────
    def run_once(self):
        self._consume_new_armed()
        now_et = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        for sym, ar in list(self._armed.items()):
            bar_df = self._latest_minute_bars(sym)
            if bar_df is None or len(bar_df) < 7:
                continue
            if not self._macd_trigger(bar_df):
                continue
            if not self._vol_spike(bar_df):
                continue
            spread = self._spread_pct(sym, bar_df.close.iloc[-1])
            if spread > ENTRY_CFG["max_spread_pct"]:
                continue
            # size & risk
            qty, risk_R = self._position_size(sym, bar_df.close.iloc[-1])
            if qty == 0:
                continue
            # submit order (first fill pct)
            first_qty = math.ceil(qty * ENTRY_CFG["first_fill_pct"])
            order_id = self.broker.market_buy(sym, first_qty)
            self._log_order(now_et, sym, first_qty, risk_R, "first_fill", order_id)
            # optional add order once price breaks pullback high
            add_pct = ENTRY_CFG["add_after_break_pct"]
            self._armed.pop(sym, None)
            # Track the add in ledger via broker_api’s callback — left as detail
            if add_pct > 0:
                self.broker.register_break_add(sym, ar.high_ref * (1 + add_pct/100), qty - first_qty)

    # ───────────── read armed CSV ─────────────
    def _consume_new_armed(self):
        if not _ARMED_CSV.exists():
            return
        with _ARMED_CSV.open() as f:
            f.seek(self._armed_pos)
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 3 or parts[0] == "ts":
                    continue
                ts = dt.datetime.fromisoformat(parts[0]).astimezone(_ET)
                sym = parts[1]
                price = float(parts[2])
                self._armed[sym] = ArmedAlert(sym, price, ts)
            self._armed_pos = f.tell()

    # ───────────── trigger checks ─────────────
    def _latest_minute_bars(self, sym: str):
        bars = self.ib.reqHistoricalData(Stock(sym, "SMART", "USD"), "", "10 mins", "1 min", "TRADES", True, 1, False, [])
        if not bars:
            return None
        return pd.DataFrame([{"open": b.open, "close": b.close, "volume": b.volume} for b in bars])

    def _macd_trigger(self, df: pd.DataFrame):
        fast, slow, sig = ENTRY_CFG["macd_fast"], ENTRY_CFG["macd_slow"], ENTRY_CFG["macd_signal"]
        ema_fast = df.close.ewm(span=fast).mean()
        ema_slow = df.close.ewm(span=slow).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=sig).mean()
        return macd.iloc[-2] < signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]

    def _vol_spike(self, df: pd.DataFrame):
        ratio = ENTRY_CFG["vol_spike_ratio"]
        avg5 = df.volume.iloc[-6:-1].mean()
        return df.volume.iloc[-1] >= avg5 * ratio

    def _spread_pct(self, sym: str, last: float):
        q = self.ib.reqMktData(Stock(sym, "SMART", "USD"), "233", snapshot=True)
        self.ib.sleep(0.3)
        if not q.bid or not q.ask:
            return 99.0
        return (q.ask - q.bid) / last * 100

    # ─────────── position sizing / risk ───────────
    def _position_size(self, sym: str, price: float):
        R_dollar = self.ledger.risk_unit()
        stop_dist = price * 0.01  # 1% placeholder — to be replaced by ATR stop from config
        qty = math.floor(R_dollar / stop_dist)
        if qty <= 0:
            return 0, 0.0
        if not self.ledger.ok_to_trade(sym, price, qty, stop_dist):
            return 0, 0.0
        risk_R = (qty * stop_dist) / R_dollar
        return qty, risk_R

    # ───────────── logging / publish ─────────────
    def _log_order(self, ts, sym, qty, risk_R, comment, order_id):
        if self._publish_mode == "csv":
            with _ENTRY_CSV.open("a", newline="") as f:
                csv.writer(f).writerow([ts.isoformat(timespec="seconds"), sym, "BUY", qty, "pending", f"{risk_R:.2f}", comment])
        else:
            self._redis_cli.publish(
                ENTRY_CFG.get("redis_channel", "entry_orders"),
                json.dumps({"ts": ts.isoformat(timespec="seconds"), "symbol": sym, "qty": qty, "riskR": risk_R, "comment": comment, "id": order_id})
            )
        _LOG.info("ORDER %s %s x%d (%s)", sym, qty, comment, order_id)

# ───────────────────────── CLI ────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    eng = EntryEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            _LOG.exception(exc)
            eng.ib.sleep(60)
