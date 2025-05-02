import sys
import pathlib
import datetime as dt
import yaml
import json
from zoneinfo import ZoneInfo

from backtest.data_adapter       import CSVDataProvider
from backtest.utils              import trading_minutes, discover_universe
from engines.scanner_engine      import ScannerEngine
from engines.watch_engine        import WatchEngine
from engines.entry_engine        import EntryEngine
from engines.exit_engine         import ExitEngine
from libs.backtest_ledger        import BacktestLedger

# project root for config and data
PROJECT_ROOT = pathlib.Path(__file__).parents[1]

def run_backtest_for(date: dt.date, universe: list[str]):
    # 1) load master config
    with open(PROJECT_ROOT / "config.yml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    session_windows = cfg["scanner"]["session_windows"]
    bt_cfg          = cfg["backtest"]
    initial_cash    = bt_cfg["initial_cash"]
    settled_only    = bt_cfg["settled_only"]
    max_positions   = bt_cfg.get("max_positions", None)

    # 2) prepare an output folder for this run
    runs_base = PROJECT_ROOT / bt_cfg["results_dir"]
    run_dir   = runs_base / date.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    # 3) wire up data provider + backtest-ledger + engines
    date_str = date.strftime("%Y%m%d")
    provider = CSVDataProvider(PROJECT_ROOT, date_str)
    ledger   = BacktestLedger(
        initial_cash=initial_cash,
        settled_only=settled_only,
        max_positions=max_positions
    )

    scanner = ScannerEngine(provider)
    watch   = WatchEngine(provider)
    entry   = EntryEngine(provider); entry.ledger = ledger
    exit    = ExitEngine(provider);  exit.ledger  = ledger

    tz = ZoneInfo("America/New_York")

    # 4) minute-by-minute loop
    for ts in trading_minutes(date, session_windows):
        ts = ts.tz_localize(tz)
        provider.set_current_time(ts)

        scanner.run_once()
        watch.run_once()
        entry.run_once()
        exit.run_once()

    # 5) dump summary + trades
    summary = ledger.summary()
    trades  = ledger.get_trades()

    summary_path = run_dir / "summary.json"
    trades_path  = run_dir / "trades.json"

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    with open(trades_path, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)

    print("Backtest complete.")
    print(f"Results saved to {run_dir}")
    print(f" • Summary: {summary_path.name}")
    print(f" • Trades:  {trades_path.name}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m backtest.runner YYYYMMDD")
        sys.exit(1)

    try:
        date = dt.datetime.strptime(sys.argv[1], "%Y%m%d").date()
    except ValueError:
        print("Invalid date format. Use YYYYMMDD")
        sys.exit(1)

    universe = discover_universe(PROJECT_ROOT, date.strftime("%Y%m%d"))
    print(f"Running backtest for {len(universe)} symbols: {universe}")
    run_backtest_for(date, universe)
