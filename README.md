# Momentum Quant Backtester

This project provides a framework for backtesting a momentum-based intraday trading strategy using historical minute-bar data.

## Features

- **CSV data provider**: Load per-minute OHLCV bars from CSV (or switch to Yahoo Finance).
- **Configurable backtest parameters**:  
  - `initial_cash`: starting buying power  
  - `settled_only`: enable T+2 settlement lockup  
  - `max_positions`: limit on simultaneous open positions  
- **Modular engines**:
  - **ScannerEngine**: scans universe each minute for entry signals  
  - **WatchEngine**: tracks potential setups after scan  
  - **EntryEngine**: executes entry orders based on signals and cash  
  - **ExitEngine**: closes positions based on stops, profit targets, and end-of-day  
- **Backtest ledger**: simulates cash, positions, P/L, and trade history  
- **Results**: outputs `summary.json` and `trades.json` per run  

## Installation

```bash
git clone <repo-url>
cd momentum-quant
python -m venv .venv
source .venv/bin/activate      # or `.venv\Scripts\activate` on Windows
pip install -r requirements.txt
```

## Configuration

Edit `config.yml` to set your parameters. Example:

```yaml
scanner:
  session_windows:          # trading session windows (NY time)
    - [09:30, 16:00]

backtest:
  data_provider:   yfinance         # or csv
  store_bars:      false
  results_dir:     backtest/runs
  initial_cash:    3000
  settled_only:    true
  max_positions:   10
```

## Usage

Prepare your raw CSV minute-bars under `data/<YYYYMMDD>/`. Then run:

```bash
python -m backtest.runner YYYYMMDD
```

This will:

1. Discover your universe via `backtest/utils.py`.  
2. Simulate intraday trading minute-by-minute.  
3. Save `summary.json` and `trades.json` in `backtest/runs/<timestamp>/`.

## Git: Ignoring Artifacts

Make sure your `.gitignore` includes:

```
backtest/runs/
*.pyc
.venv/
```

## Extending

- Swap out data providers (`CSVDataProvider`, `YFinanceProvider`).  
- Tweak entry/exit logic in the respective engine classes.  
- Add metrics or plotters in post-processing.
