# Quant Trading Strategy Overview

## 1. Universe Screen
- **Price**: $2 – $20  
- **Relative Volume (RV)**: ≥ 5×  
- **Average Volume**: ≥ 5 M shares/day  
- **Float**: < 50 M shares  
- **Today’s % Change (vs. previous close)**: ≥ +10%  
- **Market-Internals**: TICK, A/D line filter  

## 2. Preprocessing / Feature Engineering
For each 1-min bar DataFrame, compute:
- **VWAP** intraday  
- **MACD** & **Signal** line  
- **EMA₉**, **EMA₂₀**, **EMA₂₀₀**  
- (Existing: returns, MA5 vs MA15 → momentum)  

## 3. Entry Logic
1. **New-high green bar** on above-avg volume  
2. **Micro pullback**: retrace ~0.5% off that high  
3. **Indicator confirmations**:
   - MACD > Signal  
   - Price > VWAP  
   - Price > EMA₉ (and ideally EMA₉ > EMA₂₀ > EMA₂₀₀ for trend alignment)  
4. **Signal trigger**: price closes back above the pullback low/high  
5. **Order**: limit at pullback low (or market on bar close)  

## 4. Exit Logic
- **Initial stop-loss**: low of the pullback bar or 1× ATR(14), whichever is wider  
- **Trailing stop**: once P/L ≥ X%, trail at EMA₉ or Y% behind current high  
- **Profit target**: optional fixed R:R or let the trailing stop run  
- **Time stop**: exit any open position 15 minutes before market close  

## 5. Position Sizing & BR Rules
- **Base risk**: 0.5–1% equity per trade  
- **Bullish internals** (TICK > +500):  
  - ↑ size by +20%  
  - ↓ pullback requirement (e.g. 0.3% instead of 0.5%)  
- **Neutral**: standard sizing/filters  
- **Bearish internals** (TICK < –500):  
  - ↓ size by 50% or skip  
  - ↑ pullback requirement (e.g. 0.7%)  
  - ↑ RV threshold (e.g. 6×)  
- **Daily loss limit**: stop trading after –2%  

## 6. Additional Enhancements
- **Volatility-adjusted sizing & stops**  
  - Use ATR for stop distances and position sizing  
- **Trend-strength filter**  
  - ADX(14) ≥ 20; multi-timeframe EMA alignment on 5/15-min charts  
- **Liquidity & execution realism**  
  - Bid-ask spread filter; level-II checks  
  - Slippage & commissions modeling  
- **Regime & market-internals enhancements**  
  - VIX, SPY relative strength to adjust aggressiveness  
  - Earnings/holiday filters  
- **Portfolio & risk controls**  
  - Max concurrent trades; sector diversification  
- **Parameter optimization & robustness**  
  - Walk-forward testing, Monte Carlo, sensitivity analysis  
