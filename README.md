# Momentum Quant Trading Framework

A live + backtestable momentum day-trading system. Designed to be:
- **Paper-ready** in real-time with IBKR (Interactive Brokers)
- **Backtestable** down to 1-minute granularity
- Config-driven, modular, and expandable

---

## 🔧 Core Engines (Live Trading Pipeline)

| Stage | File | Description |
|-------|------|-------------|
| 0 | `scanner_engine.py` | Scans top % gainers every minute; filters by rel-vol, float, price, spread |
| 1 | `watch_engine.py` | Tracks alerts; validates pullbacks (VWAP hold, red-bars, low vol) |
| 2/3 | `entry_engine.py` | Triggers MACD+volume entry; first-fill + add logic; uses risk engine |
| 4 | `exit_engine.py` | Moves stops via staircase; exits on red bars or 9-EMA trail |

Each engine writes to `/alerts/` and can optionally publish to Redis.

---

## ✅ Live Trading Setup

```bash
git clone https://github.com/yourname/momentum-quant.git
cd momentum-quant
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Then create `config.yml` in root with:
```yaml
account:
  ib_port: 7497
  paper_account: "DUK756273"
```

Start IBKR TWS in **Paper** mode, then run:
```bash
python scripts/run_live.py
```

Live scanner, pullback monitor, entry trigger, and exit ladder will all run every second. All logic is single-threaded for safety.

---

## ⚙️ Config Structure (`config.yml`)

Tunable params include:
- `scanner`: time windows, float limit, rel-vol thresholds
- `watch`: pullback bar count, VWAP hold, red/green volume ratio
- `entry`: MACD settings, vol-spike %, spread filter, add-on trigger
- `exit`: stop R, stair-step targets, 9-EMA trail, first-red exit %
- `risk`: 1 R = % of equity, soft/hard cap, daily max risk

Full sample included in the repo root.

---

## 📈 Backtesting

```bash
python backtest/driver.py --tickers AAPL,AMD --date 2025-04-01
```

Produces:
- `/backtest/runs/YYYY-MM-DD/trades.csv`
- Live-like simulation using `SimBroker` & `StubLedger`

To implement full engine-level backtesting, swap real broker/ledger with mocks. Entry/Exit logic is identical.

---

## 📂 Project Layout
```
momentum-quant/
├── config.yml               # all tunables
├── data/                    # raw + processed minute bars
├── alerts/                  # runtime alert + trade logs
├── libs/
│   ├── broker_api.py        # ib_insync wrapper
│   ├── ledger.py            # risk model
├── engines/                 # scanner → exit stages
├── scripts/
│   └── run_live.py          # main loop for live trading
├── backtest/
│   └── driver.py            # simplified backtest driver
```

---

## 📋 Prerequisites
- Python 3.9+
- IBKR TWS (or IB Gateway) running in **paper** mode
- Market-data subscriptions (even in paper!)

```bash
pip install -r requirements.txt
```

---

## 🤝 Contributing
Pull requests welcome. Please test new features and follow modular engine format.

---

## 🪪 License
MIT © Bryan Martinez
