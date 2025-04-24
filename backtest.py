#!/usr/bin/env python3
"""
backtest.py

Combine entry & exit signals for P&L.
Buys 1 share at entry_price, sells at exit_price.
Commission: $0.0005/share, minimum $1 per order.

Usage:
  python backtest.py --date YYYY-MM-DD

Outputs:
  backtest_results.csv
"""
import os
import argparse
import pandas as pd

# CONFIG
SIGNALS_DIR         = os.path.join('data', 'signals')
COMMISSION_PER_SHARE = 0.0005
MIN_COMMISSION       = 1.0

def parse_args():
    p = argparse.ArgumentParser(description='Backtest a single date')
    p.add_argument('--date', required=True, help='Date in YYYY-MM-DD')
    return p.parse_args()

def calc_commission(shares=1):
    return max(shares * COMMISSION_PER_SHARE, MIN_COMMISSION)

def main():
    args = parse_args()
    date = args.date

    sig_file   = os.path.join(SIGNALS_DIR, f'signals_{date}.csv')
    exit_file  = os.path.join(SIGNALS_DIR, f'exits_{date}.csv')
    sig_df     = pd.read_csv(sig_file, parse_dates=['timestamp'])
    exit_df    = pd.read_csv(exit_file, parse_dates=['entry_ts','exit_ts'])

    # join on ticker & entry timestamp
    df = pd.merge(sig_df, exit_df,
                  left_on=['ticker','timestamp'],
                  right_on=['ticker','entry_ts'],
                  how='inner')

    trades = []
    for _, r in df.iterrows():
        entry_price = r['entry_price']
        exit_price  = r['exit_price']
        entry_comm  = calc_commission()
        exit_comm   = calc_commission()
        pnl         = (exit_price - entry_price) - (entry_comm + exit_comm)

        trades.append({
            'ticker':          r['ticker'],
            'entry_ts':        r['timestamp'],
            'entry_price':     entry_price,
            'entry_commission':entry_comm,
            'exit_ts':         r['exit_ts'],
            'exit_price':      exit_price,
            'exit_commission': exit_comm,
            'pnl':             round(pnl, 4),
            'regime':          r.get('regime', '')
        })

    if not trades:
        print("ℹ️  No trades to backtest.")
        return

    result_df = pd.DataFrame(trades)
    result_df.to_csv('backtest_results.csv', index=False)

    total_pnl = result_df['pnl'].sum()
    win_rate  = (result_df['pnl'] > 0).mean()

    print("\nBacktest Summary:")
    print(result_df.describe(), "\n")
    print(f"Net P&L:   ${total_pnl:.2f}")
    print(f"Win rate:  {win_rate:.2%}")

if __name__ == '__main__':
    main()
