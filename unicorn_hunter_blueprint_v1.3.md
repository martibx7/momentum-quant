
# Unicorn Hunter Momentum Strategy – Blueprint v1.3
*Author – Bryan Martinez*  *Updated: 30 Apr 2025*

---

## 0  Mission
Hunt **1‑5 true second‑wave micro‑cap momentum plays per session** (cash account < 25 k) and milk them, not shotgun every spike.

## 1  State Machine
```
alert → watch → armed → entry → closed
  ▲                  │
  └─ purge ──────────┘   (30‑min timeout)
```

## 2  Scanner (Stage 0 – alert)

| Filter | Rule |
|--------|------|
| **Price** | $1 – $20 |
| **Day Move** | +10 % total **OR** +8 % intraday |
| **RV (1‑min)** | vol_now / mean(vol_sameMinute, 10 d) ≥ 3.5× (09:30‑09:45) else ≥ 5× |
| **Volume override** | alert if vol_now ≥ 250 k even if RV < threshold |
| **Float tag** | float < 20 M ⇒ `lowFloat` tag (sorted top) |
| **Spread** | bid‑ask ≤ 1.5 % |
| **Exclusions** | ETFs soft‑tagged, skip sub‑$1 pre‑spikers |
| **Halts** | tag if > 3 halts (don’t block) |
| **Session** | 09:35‑11:15 & 13:30‑15:30 ET |

*Alert payload every 60 s:* `sym, price, %chg, RV1m, vol, HOD_dist, spread, float, halt_flag`.

## 3  Watchlist (Stage 1 – watch)
Store alert snapshot; remain *watch* until pull‑back validates or 30 min elapse.

## 4  Micro‑Pullback (Stage 2 – armed)

| Rule | Value |
|------|-------|
| Red bars | 1‑2 (3rd only if closes ≥ 50 % of pole) |
| Retrace | 2‑6 % below spike‑high *or* ≤ 50 % pole length |
| Volume | mean pull‑back ≤ 40 % spike_vol **and** ≤ 200 k |
| VWAP hold | close ≥ anchored VWAP – 0.5 % & ≥ spike_base + 25 % |

`break_level = high(last_red)` → state = armed.

## 5  Entry Trigger (Stage 3 – entry)

Fire **immediately** at `break_level + $0.01` when  
1. **Fast MACD (6,13,4)** bullish, histogram ≥ 0  
2. 1‑min volume ≥ previous bar  
3. spread ≤ 1.5 %

Execution: half‑size marketable limit (ask + tick); add other half if bar closes green & > VWAP.

## 6  Risk & Exit

| Milestone | Action |
|-----------|--------|
| Entry | stop = max(2 %, $0.05, 0.3×ATR_1m) below entry or VWAP‑0.5 % |
| +1 R | stop → entry – 0.1 R |
| +2 R | stop → break‑even + tick |
| +3 R | scale ⅓ |
| +5 R | optional 2nd skim |
| **First red close < prior low** | exit remainder immediately |
| Trail | min(9‑EMA − 0.1 %, prior 2‑bar swing‑low) |
| +8 R & HOT | switch trail to anchored VWAP − 0.25 % |
| 15:55 ET | flat everything |

## 7  Market Temperature Filter

### Proxy (pre‑internals)
```
HOT  : ≥2 of (SPY,QQQ,IWM) ≥ +0.30 %
COLD : ≥2 ≤ –0.30 %  or SPY ≤ –0.50 %
NEUTRAL otherwise
```

| Mood | Risk % | Capital % | Notes |
|------|--------|-----------|-------|
| HOT | 2.5 % | 35 % | ride mode unlocked |
| NEUTRAL | 2 % | 30 % | default |
| COLD | 1.5 % | 25 % | armed→entry blocked if still cold at breakout; open trades tighten to B/E |

**Internal end‑goal:** ADD>+500, TICK_SMA>+400, UVOL/DVOL≥1.8 (swap in when feed live).

## 8  News / Filing Auto‑Purge
Instant purge if 8‑K, S‑3, 424B5, ATM shelf hits while *watch/armed*.

## 9  Commission & Slippage
* IBKR Pro Tiered all‑in ≈ $0.00373/sh, $0.35 min  
* Back‑test & paper slippage: **entry = ask+1 tick, exit = bid–1 tick**; paper fills clipped to this rule.

## 10  Data, Logging & Controls
* Pre‑serialize 10‑day minute table 09:29 (< 100 MB).  
* 2‑sec volume debounce; cache EMA state tick‑by‑tick.  
* Retry: if order not Filled/Cancelled in 2 s → cancel/replace (max 3) else panic_flat.  
* Panic button GUI shows live P/L, cash, mood.  
* Logs: one CSV per trade plus daily summary; screenshots on entries.  
* **config.yml** holds all tunables; unit/integration tests validate scanner, ledger, kill‑switches.

## 11  Bankroll & Cash‑Only Rules
Start ≈ $3 k cash.  T+1 settlement enforced.

| Metric | Formula (NEUTRAL) | HOT | COLD |
|--------|-------------------|-----|------|
| Capital/pos | 30 % settled (≤ $900) | 35 % | 25 % |
| Risk/trade | 2 % settled (≤ $60) | 2.5 % | 1.5 % |
| **Float override** | if float > 50 M ⇒ risk cap 1.5 % (HOT 2 %, COLD 1 %) |
| Daily loss stop | 4 % start‑day equity |
| Weekly DD stop | 10 % start‑week equity |
| Live positions | ≤ 1 (unlock 2 when equity ≥ 5 k) |

## 12  KPI Targets
Alerts 20‑40 • Watch→Armed ≥ 25 % • Armed→Entry 30‑40 % • Win ≥ 45 % • Avg R ≥ 2:1 • Daily DD < 2 % equity

## 13  Next Milestones
1. Back‑run 30 sessions with commissions & 1‑tick slippage.  
2. Plug float scraper & ETF mood filter into live scanner.  
3. Build panic GUI & logging dashboard.  
4. IBKR‑paper pilot (T+1 ledger & mood‑adaptive risk).
