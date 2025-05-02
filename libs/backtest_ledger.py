import datetime
from types import SimpleNamespace

class Position(SimpleNamespace):
    """
    A lightweight container for a live position.
    Fields:
      - qty: float
      - entry_price: float
      - stop_price: float | None
    """
    pass

class BacktestLedger:
    def __init__(self,
                 initial_cash: float,
                 settled_only: bool,
                 max_positions: int | None = None):
        self.initial_cash   = initial_cash
        self.settled_only   = settled_only
        self.max_positions  = max_positions

        self.cash           = initial_cash
        self.unsettled_cash = 0.0
        # positions: symbol -> {"qty": float, "avg_price": float, "stop_price": float|None}
        self.positions      = {}
        self.trades         = []

    def record_fill(self, symbol: str, qty: float, price: float, timestamp: datetime.datetime):
        cost = qty * price
        self.cash -= cost

        pos = self.positions.get(symbol, {"qty": 0.0, "avg_price": 0.0, "stop_price": None})
        total_qty = pos["qty"] + qty

        if total_qty != 0:
            pos["avg_price"] = (pos["qty"] * pos["avg_price"] + cost) / total_qty
        pos["qty"] = total_qty

        # leave pos["stop_price"] alone here; engines should update it if they set stops
        self.positions[symbol] = pos

        self.trades.append({
            "time":   timestamp.isoformat(),
            "symbol": symbol,
            "qty":    qty,
            "price":  price,
            "cash":   self.cash
        })

    def live_positions(self) -> dict[str, Position]:
        """
        Return a dict of symbol -> Position(qty, entry_price, stop_price)
        for all currently open (non-zero qty) positions.
        """
        live = {}
        for sym, data in self.positions.items():
            if data["qty"] != 0:
                live[sym] = Position(
                    qty=data["qty"],
                    entry_price=data["avg_price"],
                    stop_price=data.get("stop_price")
                )
        return live

    def summary(self):
        return {
            "starting_cash":   self.initial_cash,
            "ending_cash":     self.cash,
            "open_positions": {
                sym: data["qty"]
                for sym, data in self.positions.items() if data["qty"] != 0
            },
            "n_trades": len(self.trades)
        }

    def get_trades(self):
        return self.trades
