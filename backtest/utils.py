import pathlib
import pandas as pd
from typing import List

def discover_universe(repo_root: pathlib.Path, date_str: str) -> List[str]:
    """
    Discover all tickers for the given date by scanning data/raw for files matching
    {symbol}_{date_str}_1min.csv and returning the symbol list.
    """
    raw_dir = repo_root / "data" / "raw"
    pattern = f"*_{date_str}_1min.csv"
    files = raw_dir.glob(pattern)
    symbols = []
    for fp in files:
        name = fp.stem  # e.g. "AAPL_20250428_1min"
        parts = name.split("_")
        if len(parts) >= 3 and parts[-1] == "1min":
            symbols.append(parts[0])
    return sorted(symbols)

def trading_minutes(date: pd.Timestamp, session_windows: List) -> pd.DatetimeIndex:
    """
    Generate a union of minute-level timestamps for the given date across all session
    windows. Supports windows defined as [start, end] lists or {start, end} dicts.

    Example session_windows formats:
      - ["09:30", "16:00"]
      - {start: "09:35", end: "11:15"}
    """
    minutes_idx = None
    for win in session_windows:
        if isinstance(win, dict):
            start_s = win["start"]
            end_s = win["end"]
        else:
            start_s, end_s = win

        start_dt = pd.to_datetime(f"{date} {start_s}")
        end_dt   = pd.to_datetime(f"{date} {end_s}")
        rng = pd.date_range(start=start_dt, end=end_dt, freq="min")

        if minutes_idx is None:
            minutes_idx = rng
        else:
            minutes_idx = minutes_idx.union(rng)

    return minutes_idx
