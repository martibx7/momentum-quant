# bankroll_mgmt.py
# Library for position sizing and risk allocation based on account bankroll and market regime

# Default total account size (can be overridden in function calls)
ACCOUNT_SIZE = 3000.0  # USD

# Risk percentage by barometer regime
RISK_BY_REGIME = {
    'strong_bull': 0.02,  # risk 2% of account
    'neutral':     0.01,  # risk 1%
    'bearish':     0.005, # risk 0.5%
}

import math

def get_risk_pct(regime: str) -> float:
    """
    Return the fraction of account to risk based on regime.
    Falls back to neutral if regime unknown.
    """
    return RISK_BY_REGIME.get(regime, RISK_BY_REGIME['neutral'])


def calc_position_size(
        entry_price: float,
        stop_price: float,
        account_size: float = ACCOUNT_SIZE,
        regime: str = 'neutral'
) -> int:
    """
    Calculate number of shares to buy so that the dollar risk
    (entry_price - stop_price) * shares = account_size * risk_pct.

    entry_price: the price at which position is opened
    stop_price: the price at which position is stopped out
    account_size: total capital
    regime: market regime label for risk_pct selection

    Returns integer share count (floored)."""
    risk_pct = get_risk_pct(regime)
    risk_amount = account_size * risk_pct
    per_share_risk = abs(entry_price - stop_price)
    if per_share_risk <= 0:
        raise ValueError(f"Invalid risk per share: entry={entry_price}, stop={stop_price}")
    shares = math.floor(risk_amount / per_share_risk)
    return max(shares, 1)
