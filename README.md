# Momentum Quant Trading Framework

A local momentum-based algorithmic trading system with end-to-end backtesting and execution scaffolding.

## Overview
This repo implements a full daily backtest and live-ready workflow leveraging minute-level data and a dynamic ETF barometer. The core pipeline for a given date is:

1. **download_universe_samples.py**  
   • Fetch raw 1-min bars for your ticker universe (from `data/universe.txt`) plus SPY/QQQ/IWM barometers → `data/raw/`
2. **preprocess.py**  
   • Resample & clean raw bars; filter by price band ($2–$20); compute VWAP, EMAs, MACD, ATR, ADX, momentum → `data/processed/`
3. **universe_filter.py**  
   • Static screener on R2K universe (move ≥10%, rel-vol ≥5×, avg-vol ≥5M, float <50M) → `data/signals/universe_filtered_<date>.csv`
4. **generate_signals.py**  
   • For filtered symbols, detect breakout+pullback entries with indicator checks and regime‑adjusted thresholds (SPY/QQQ/IWM barometer) → `data/signals/signals_<date>.csv`
5. **exit_signals.py**  
   • Attach exits per entry (stop-loss, momentum end, MACD/VWAP crosses, time-stop by regime) → `data/signals/exits_<date>.csv`
6. **backtest.py** (or **run_full_backtest.py**)  
   • Combine entries & exits into per-trade P&L, net P&L, win-rate; outputs `data/backtest/trades_<date>.csv`

## Prerequisites
- Python 3.9+  
- `pip install -r requirements.txt` (includes pandas, yfinance, python-dotenv, ib_insync, etc.)  
- (Optional) Interactive Brokers TWS/IB Gateway for live execution

## Setup
```bash
git clone https://github.com/martibx7/momentum-quant.git
cd momentum-quant
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

If using IBKR for live orders, create a `.env` in project root:
```ini
IB_HOST=127.0.0.1
IB_PORT=7497
IB_CLIENT_ID=1
```

## Usage
Run the full backtest for a specific date:
```bash
# 1) Download raw data
python download_universe_samples.py --date 2025-04-23

# 2) Preprocess indicators
python preprocess.py --date 2025-04-23

# 3) Static universe screen
python universe_filter.py --date 2025-04-23

# 4) Generate entry signals
python generate_signals.py --date 2025-04-23

# 5) Generate exit signals
python exit_signals.py --date 2025-04-23

# 6) Backtest results
python backtest.py --date 2025-04-23
# (or python run_full_backtest.py --date 2025-04-23)
```

To run only parts (e.g., live orders), navigate to the `executions/` folder and follow its README.

## File Structure
```
momentum-quant/
├── connect.py                # IBKR connection helper
├── download_universe_samples.py
├── preprocess.py
├── universe_filter.py
├── generate_signals.py
├── exit_signals.py
├── backtest.py
├── run_full_backtest.py      # optional consolidated runner
├── requirements.txt
├── data/
│   ├── raw/                  # raw 1-min CSVs
│   ├── processed/            # cleaned & indicator-enriched data
│   ├── signals/              # filtered universe, entries & exits CSVs
│   └── backtest/             # backtest trade-level outputs
├── universe.txt              # static ticker list (e.g., Russell-2000)
├── strategies/               # placeholder for additional strategies
├── executions/               # live order logic (paper/live)
├── logs/                     # execution & error logs
├── .env                      # IBKR credentials (gitignored)
└── README.md
```

## Contributing
Pull requests and issues welcome! Please adhere to the existing code style and add tests for new functionality.

## License
MIT © Bryan Martinez
