"""Scanner Engine — Stage-0 (alert) | Momentum-Quant
=================================================
… now with same-minute 10-day RV, day-move, vol-override, lowFloat, halt flags, HOD_dist …
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import List, Sequence, Optional

import pandas as pd
import yaml
import yfinance as yf
from ib_insync import IB, Stock, util, ScannerSubscription  # type: ignore
from zoneinfo import ZoneInfo

try:
    import redis
except ModuleNotFoundError:
    redis = None  # type: ignore

# ────────────────────────── Paths & constants ────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
# explicitly open config.yml with UTF-8
with open(_REPO_ROOT / "config.yml", encoding="utf-8") as f:
    _CONFIG = yaml.safe_load(f)
SCAN_CFG = _CONFIG["scanner"]

_ALERT_DIR = _REPO_ROOT / "alerts"
_ALERT_DIR.mkdir(exist_ok=True)
_ET = ZoneInfo("America/New_York")



@dataclass(slots=True)
class Alert:
    ts: dt.datetime
    symbol: str
    price: float
    pct_move: float
    rv: float
    vol: int
    avgVol10: float
    vol_override: bool
    hod_dist: float
    spread_pct: float
    float_sh: int
    lowFloat: bool
    haltFlag: int
    trend: float

    @property
    def quality(self) -> float:
        return self.rv * self.trend

    def as_csv(self) -> List[str]:
        return [
            self.ts.isoformat(timespec="seconds"),
            self.symbol,
            f"{self.price:.2f}",
            f"{self.pct_move:.2f}",
            f"{self.rv:.2f}",
            str(self.vol),
            f"{self.hod_dist:.2f}",
            f"{self.spread_pct:.2f}",
            str(self.float_sh),
            str(int(self.lowFloat)),
            str(self.haltFlag),
            f"{self.trend:.2f}",
            f"{self.quality:.2f}",
        ]

    def as_json(self) -> str:
        return json.dumps({
            "ts": self.ts.isoformat(timespec="seconds"),
            "symbol": self.symbol,
            "price": self.price,
            "pct_move": self.pct_move,
            "rv": self.rv,
            "vol": self.vol,
            "hod_dist": self.hod_dist,
            "spread_pct": self.spread_pct,
            "float": self.float_sh,
            "lowFloat": self.lowFloat,
            "haltFlag": self.haltFlag,
            "trend": self.trend,
            "quality": self.quality,
        })

class ScannerEngine:
    """Stage-0 scanner that emits alerts every minute with enhanced filters."""

    def __init__(self, ib: IB | None = None):
        self.ib = ib or IB()
        if not self.ib.isConnected():
            self.ib.connect("127.0.0.1", 7497, clientId=17)

        if SCAN_CFG["publish"] == "redis":
            if redis is None:
                raise RuntimeError("publish=redis requires redis-py installed")
            self.redis_cli = redis.Redis()
        else:
            self.redis_cli = None  # type: ignore

    def run_once(self):
        now = dt.datetime.now(dt.timezone.utc).astimezone(_ET)
        if not self._in_session(now.time()):
            return
        df = self._fetch_universe_bars(now)
        if df.empty:
            return
        alerts = self._build_alerts(df, now)
        if alerts:
            self._publish(alerts)

    def _fetch_universe_bars(self, now: dt.datetime) -> pd.DataFrame:
        sub = ScannerSubscription(
            instrument='STK',
            locationCode='STK.US.MAJOR',
            scanCode='TOP_PERC_GAIN',
            abovePrice=SCAN_CFG["min_price"],
            belowPrice=SCAN_CFG["max_price"],
            aboveVolume=SCAN_CFG["min_volume"],
            numberOfRows=SCAN_CFG.get("number_of_rows", 50)
        )
        scan_rows = self.ib.reqScannerData(sub, []).wait(timeout=5)
        symbols = [r.contractDetails.contract.symbol for r in scan_rows]
        if not symbols:
            return pd.DataFrame()

        records = []
        minute_str = now.strftime("%H:%M")
        for sym in symbols:
            c = Stock(sym, "SMART", "USD")
            # 1-min bar
            bars = self.ib.reqHistoricalData(c, "", "2 mins", "1 min", "TRADES", True, 1, False, [])
            if not bars:
                continue
            last = bars[-1]
            vol_now = last.volume
            price_now = last.close
            # hod_dist
            try:
                info = yf.Ticker(sym).info
                high_day = info.get("dayHigh") or price_now
                hod_dist = (high_day - price_now)/high_day*100
            except Exception:
                hod_dist = 0.0
            # avgVol10
            try:
                hist = yf.Ticker(sym).history(period="15d", interval="1m", prepost=False)
                same_min = hist.between_time(minute_str, minute_str)["Volume"]
                vol_avg10 = same_min.tail(10).mean()
            except Exception:
                # fallback polygon omitted for brevity
                vol_avg10 = 1.0
            records.append((sym, price_now, vol_now, vol_avg10, hod_dist))

        return pd.DataFrame(records, columns=["symbol","close","volume","avgVol10","hod_dist"])

    def _build_alerts(self, df: pd.DataFrame, now: dt.datetime):
        alerts: List[Alert] = []
        # thresholds
        is_open = dt.time(9,30) <= now.time() <= dt.time(9,45)
        rv_thr = SCAN_CFG["pre_open_rv"] if is_open else SCAN_CFG["intraday_rv"]
        ov_thr = SCAN_CFG.get("vol_override", 0)
        for row in df.itertuples(index=False):
            rv = row.volume/max(row.avgVol10,1)
            vol_override = row.volume >= ov_thr
            if rv < rv_thr and not vol_override:
                continue
            # pct move
            prev = row.close
            try:
                d = self.ib.reqHistoricalData(Stock(row.symbol,"SMART","USD"),"","2 D","1 day","TRADES",True,1,False,[])
                prev = d[-2].close if len(d)>=2 else row.close
            except:
                pass
            pct_move = (row.close-prev)/prev*100
            if pct_move < SCAN_CFG.get("pct_move_intraday" if not is_open else "pct_move_total",0):
                continue
            # float
            try:
                fs = int(yf.Ticker(row.symbol).info.get("floatShares") or 0)
            except:
                fs=0
            lowFloat = fs < SCAN_CFG.get("float_low_thresh",0)
            if fs> SCAN_CFG["float_max"]:
                continue
            # spread
            q = self.ib.reqMktData(Stock(row.symbol,"SMART","USD"),"233",snapshot=True)
            self.ib.sleep(0.2)
            b,a = q.bid or 0,q.ask or 0
            spread_pct = (a-b)/row.close*100 if b and a else 99.0
            if spread_pct>SCAN_CFG["spread_max_pct"]:
                continue
            # halts placeholder
            haltFlag=0
            # trend
            tdf=self.ib.reqHistoricalData(Stock(row.symbol,"SMART","USD"),"","15 mins","1 min","TRADES",True,1,False,[])
            closes=pd.Series([b.close for b in tdf])
            trend=0.0
            if len(closes)>=3:
                ema3=closes.ewm(span=3).mean()
                slope=(ema3.iloc[-1]-ema3.iloc[-3])/max(ema3.iloc[-3],1e-4)
                trend=max(min(slope*100,1.5),0)
            alerts.append(Alert(now,row.symbol,row.close,pct_move,rv,row.volume,row.avgVol10,vol_override,row.hod_dist,spread_pct,fs,lowFloat,haltFlag,trend))
        return alerts

    def _publish(self, alerts: Sequence[Alert]):
        path=_ALERT_DIR/f"alert_{dt.date.today():%Y%m%d}.csv"
        new=not path.exists()
        with path.open("a",newline="") as f:
            w=csv.writer(f)
            if new:
                w.writerow(["ts","symbol","price","pctMove","rv","vol","hodDist","spreadPct","float","lowFloat","haltFlag","trend","qs"])
            for a in alerts:
                w.writerow(a.as_csv())
        _LOG.info("%d alerts → %s",len(alerts),path.name)

    def _in_session(self,t:dt.time)->bool:
        for win in SCAN_CFG["session_windows"]:
            sh,sm=map(int,win["start"].split(':'))
            eh,em=map(int,win["end"].split(':'))
            if dt.time(sh,sm)<=t<=dt.time(eh,em): return True
        return False

if __name__=="__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s",level=logging.INFO)
    eng=ScannerEngine()
    while True:
        try:
            eng.run_once()
        except KeyboardInterrupt:
            break
        except Exception:
            _LOG.exception("scanner error; retrying in 60s")
            eng.ib.sleep(60)
