#!/usr/bin/env python3
"""
Rename all SYMBOL_1min_sample.csv files in data/raw/ to
SYMBOL_YYYYMMDD_1min_sample.csv based on the first timestamp in each file.
"""

import os
from glob import glob
from datetime import datetime

RAW_DIR = os.path.join('data', 'raw')

def infer_date_from_file(path):
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            # skip empty lines or the header line
            if not line or 'Date' in line or 'Datetime' in line:
                continue
            # take the first field before the comma
            ts_str = line.split(',', 1)[0]
            # try a couple of common formats
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(ts_str, fmt)
                except ValueError:
                    pass
            # try ISO fallback
            try:
                return datetime.fromisoformat(ts_str)
            except ValueError:
                pass
            # if nothing matched, give up on this line
    return None

def main():
    pattern = os.path.join(RAW_DIR, "*_1min_sample.csv")
    for path in glob(pattern):
        fname = os.path.basename(path)
        sym, rest = fname.split('_', 1)  # e.g. ["AAPL", "1min_sample.csv"]

        dt = infer_date_from_file(path)
        if not dt:
            print(f"⚠️  Skipping {fname}: could not infer date")
            continue

        date_str = dt.strftime("%Y%m%d")
        new_fname = f"{sym}_{date_str}_{rest}"
        new_path  = os.path.join(RAW_DIR, new_fname)

        if os.path.exists(new_path):
            print(f"⏭  {new_fname} already exists, skipping")
        else:
            os.rename(path, new_path)
            print(f"✅  Renamed {fname} → {new_fname}")

if __name__ == "__main__":
    main()
