"""Ledger — risk accounting & position gating
===========================================
Keeps a running book of *open* risk (R), enforces position caps, and vets each
prospective entry for compliance with **risk** section of ``config.yml``.

Exposed API
-----------
* ``risk_unit()``               → dollar value of 1 R
* ``open_R()``                  → cumulative open risk in R
* ``ok_to_trade(symbol, price, qty, stop_dist, quality)``
    Returns **True/False** and, if True, books the risk immediately.
* ``update_from_broker(broker: BrokerAPI)``
    Refreshes live positions + equity; auto‑recomputes open risk.
* ``live_positions()``          → dict[ symbol → Position ] (pass‑thru)

Implementation notes
--------------------
* Equity fetched from BrokerAPI via account summary; default 100 000 if unavailable.
* Risk = position_qty × stop_dist  ➜ expressed in dollars, converted to R.
* **soft_position_cap**: if exceeded, trade allowed only if
  ``quality >= risk.quality_threshold.throttled``.
* **hard_position_cap**: absolute limit (``null`` disables).
* Daily reset at 00:00 ET (assumes U.S. market calendar).
"""
from __future__ import annotations

import datetime as dt
import logging
import pathlib
from typing import Dict, Optional

import yaml
from ib_insync import IB  # type: ignore
from zoneinfo import ZoneInfo

_LOG = logging.getLogger(__name__)
_LOG.setLevel(logging.INFO)

# ───────────────────────── config ──────────────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = yaml.safe_load((_ROOT / "config.yml").read_text())
RISK_CFG = _CFG["risk"]
_ET = ZoneInfo("America/New_York")

# ───────────────────────── Position dataclass re‑export ────────────────────
from libs.broker_api import Position, BrokerAPI  # noqa: E402  pylint: disable=wrong-import-position

# ───────────────────────── Ledger class ─────────────────────────────────────
class Ledger:
    """Risk + position bookkeeping independent of strategy logic."""

    def __init__(self, broker: Optional[BrokerAPI] = None):
        self.broker = broker or BrokerAPI(IB())
        self._reset_for_new_day()

    # ───────── public helpers ─────────
    def risk_unit(self) -> float:
        return self.equity * RISK_CFG["R_pct_equity"]

    def open_R(self) -> float:
        return self._open_R

    def live_positions(self) -> Dict[str, Position]:
        return self._positions

    def update_from_broker(self):
        self._roll_day_if_needed()
        self._positions = self.broker.live_positions()
        self._compute_open_risk()
        self.equity = self._fetch_equity()

    def ok_to_trade(self, symbol: str, price: float, qty: int, stop_dist: float, quality: float | None = None) -> bool:
        """Gatekeeper called *before* submitting an order.
        Books risk immediately if True is returned (speculative risk booking).
        """
        self._roll_day_if_needed()
        self.update_from_broker()

        # Hard cap check
        hard_cap = RISK_CFG.get("hard_position_cap")
        if hard_cap is not None and len(self._positions) >= hard_cap:
            _LOG.warning("hard_position_cap hit (%d)", hard_cap)
            return False
        # Soft throttle check
        soft_cap = RISK_CFG["soft_position_cap"]
        if len(self._positions) >= soft_cap:
            thresh = RISK_CFG["quality_threshold"]["throttled"]
            if quality is None or quality < thresh:
                _LOG.info("soft cap active; quality %.2f < %.2f", quality or 0, thresh)
                return False
        # Risk budget check
        R_dollar = self.risk_unit()
        risk_dollar = qty * stop_dist
        risk_R = risk_dollar / R_dollar
        if self._open_R + risk_R > RISK_CFG["daily_max_R"]:
            _LOG.warning("daily_max_R exceeded (%.2f + %.2f > %d)", self._open_R, risk_R, RISK_CFG["daily_max_R"])
            return False
        # Book it
        self._open_R += risk_R
        _LOG.info("risk booked %s %.2f R (open=%.2f)", symbol, risk_R, self._open_R)
        return True

    # ───────── internals ─────────
    def _compute_open_risk(self):
        R_val = self.risk_unit()
        total = 0.0
        for p in self._positions.values():
            stop_dist = abs(p.entry_price - p.stop_price) or p.entry_price * 0.01  # fallback
            total += (p.qty * stop_dist) / R_val
        self._open_R = total

    def _fetch_equity(self):
        try:
            acc = self.broker.ib.managedAccounts()[0]
            summary = self.broker.ib.accountSummary(acc, "NetLiquidation")
            if summary:
                return float(summary[0].value)
        except Exception as exc:  # pylint:disable=broad-except
            _LOG.warning("equity fetch failed: %s", exc)
        return 100_000.0  # default if unavailable

    def _roll_day_if_needed(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET).date()
        if now != self._today:
            self._reset_for_new_day()

    def _reset_for_new_day(self):
        self._today = dt.datetime.now(dt.timezone.utc).astimezone(_ET).date()
        self._positions: Dict[str, Position] = {}
        self.equity: float = self._fetch_equity()
        self._open_R: float = 0.0
        _LOG.info("Ledger reset for %s", self._today)
