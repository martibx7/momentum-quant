# Momentum Quant

A local, IBKR-powered momentum trading algorithm framework in Python.

## Overview
This repository contains the scaffolding for a momentum-based trading system using Interactive Brokers (IBKR) for data ingestion and order execution. It includes:

- **connect.py**: IBKR connection helper
- **data_ingestion.py**: Historical data pull and CSV export
- **executions/**: Live trading order logic
- **strategies/**: Signal generation and backtesting scripts
- **data/**: Raw and processed data files
- **logs/**: Execution and error logs

## Prerequisites
- Python 3.9+
- An IBKR account (paper trading)
- TWS or IB Gateway running in paper mode
- Git

## Setup
1. **Clone the repo**
   ```bash
   git clone https://github.com/martibx7/momentum-quant.git
   cd momentum-quant
   ```
2. **Create & activate a virtual environment**
   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # macOS/Linux
   source venv/bin/activate
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
4. **Create a `.env` file** in the project root:
   ```ini
   IB_HOST=127.0.0.1
   IB_PORT=7497
   IB_CLIENT_ID=1
   ```
5. **Verify the IBKR connection**
   ```bash
   python connect.py
   ```

## Usage
- **Ingest historical data**
  ```bash
  python data_ingestion.py
  ```
- **Run strategy backtests**
  Scripts will live in `strategies/` with clear entry points.
- **Execute live trades**
  Execution logic will be under `executions/`, respecting cash-only buying power.

## File Structure
```
momentum-quant/
├── connect.py
├── data_ingestion.py
├── strategies/
├── executions/
├── data/
│   └── raw/
├── logs/
├── requirements.txt
├── .env
├── .gitignore
└── README.md
```

## Contributing
Feel free to open issues or submit pull requests for enhancements.

## License
MIT © [Your Name]

