"""BrokerAPI — thin wrapper around ib_insync
===========================================
Abstracts common order operations so engines don’t fuss with TWS quirks.
Only supports **market** entries/exits and **stop-loss** adjustments for now.
Extend later with bracket orders or synthetic OCO when needed.

Public methods
--------------
* ``market_buy(symbol, qty)``  → returns orderId
* ``market_sell(symbol, qty)`` → returns orderId
* ``move_stop_loss(symbol, stop_px)``
* ``register_break_add(symbol, trigger_px, qty)`` — places a stop-limit buy
  that fires when price ≥ trigger_px (used for add-on entries)
* ``refresh_positions()`` returns {symbol: Position}

Position dataclass exposed for **ledger** consumption.

Notes
-----
* All prices **USD** and assumed SMART routing.
* Wrapper auto-creates a Stock contract (SMART/USD) and caches it.
* Orders are *DAY* by default; stop-loss orders are *GTC*.
* Uses ib_insync’s ``OrderStatus`` callbacks to update internal books.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional

from ib_insync import IB, Contract, Order, Stock, util  # type: ignore

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ─────────────────────────── data class ────────────────────────────────────
@dataclass(slots=True)
class Position:
    symbol: str
    qty: int
    entry_price: float
    stop_price: float

# ─────────────────────────── BrokerAPI ─────────────────────────────────────
class BrokerAPI:
    """Simplified broker façade for engines."""

    def __init__(self, ib: Optional[IB] = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=21)  # updated to avoid clientId conflict
        self._contract_cache: Dict[str, Stock] = {}
        self._positions: Dict[str, Position] = {}
        # register global fill handler
        self.ib.execDetailsEvent += self._on_fill

    # ───────────────────────── order helpers ──────────────────────────────
    def market_buy(self, symbol: str, qty: int) -> int:
        contract = self._stock(symbol)
        order = Order(action="BUY", totalQuantity=qty, orderType="MKT", tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        _LOG.info("MKT BUY %s x%d (id=%s)", symbol, qty, trade.order.permId)
        return trade.order.permId

    def market_sell(self, symbol: str, qty: int) -> int:
        contract = self._stock(symbol)
        order = Order(action="SELL", totalQuantity=qty, orderType="MKT", tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        _LOG.info("MKT SELL %s x%d (id=%s)", symbol, qty, trade.order.permId)
        return trade.order.permId

    def move_stop_loss(self, symbol: str, stop_px: float):
        pos = self._positions.get(symbol)
        if not pos:
            _LOG.warning("move_stop_loss called but no position in %s", symbol)
            return
        contract = self._stock(symbol)
        order = Order(action="SELL", totalQuantity=pos.qty, orderType="STP",
                      auxPrice=round(stop_px, 2), tif="GTC")
        trade = self.ib.placeOrder(contract, order)
        pos.stop_price = stop_px
        _LOG.info("STOP LOSS moved %s -> %.2f (id=%s)", symbol, stop_px, trade.order.permId)

    def register_break_add(self, symbol: str, trigger_px: float, qty: int):
        contract = self._stock(symbol)
        order = Order(action="BUY", totalQuantity=qty, orderType="STP LMT",
                      auxPrice=round(trigger_px, 2), lmtPrice=round(trigger_px * 1.002, 2), tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        _LOG.info("BREAK-ADD %s +%d @%.2f (id=%s)", symbol, qty, trigger_px, trade.order.permId)
        return trade.order.permId

    # ───────────────────────── position snapshot ───────────────────────────
    def live_positions(self) -> Dict[str, Position]:
        # ensure up-to-date via reqPositions
        acc = self.ib.managedAccounts()[0]
        self.ib.reqPositions()
        ib_pos = {p.contract.symbol: p for p in self.ib.positions() if p.account == acc}
        for sym, p in ib_pos.items():
            self._positions.setdefault(sym, Position(sym, int(p.position), p.avgCost, stop_price=0))
        return self._positions

    # ───────────────────────── internals ───────────────────────────────────
    def _stock(self, symbol: str) -> Stock:
        if symbol not in self._contract_cache:
            self._contract_cache[symbol] = Stock(symbol, "SMART", "USD")
        return self._contract_cache[symbol]

    def _on_fill(self, trade, fill):  # ib_insync callback
        sym = trade.contract.symbol
        pos = self._positions.get(sym)
        if trade.order.action == "BUY":
            if pos:
                # average entry price update
                new_qty = pos.qty + fill.execution.shares
                pos.entry_price = ((pos.entry_price * pos.qty) + (fill.execution.price * fill.execution.shares)) / new_qty
                pos.qty = new_qty
            else:
                self._positions[sym] = Position(sym, fill.execution.shares, fill.execution.price, stop_price=0)
        elif trade.order.action == "SELL":
            if pos:
                pos.qty -= fill.execution.shares
                if pos.qty <= 0:
                    self._positions.pop(sym, None)
        _LOG.debug("Fill %s %s x%d @%.2f", sym, trade.order.action, fill.execution.shares, fill.execution.price)
