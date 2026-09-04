"""Upstox public endpoints (no credentials): instrument master + historical candles for NSE.

The instrument master (NSE.json.gz, ~75k rows) is cached on disk for 24h and used to map
``NSE:RELIANCE`` -> ``NSE_EQ|INE002A01018``."""

from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..errors import DataError, SymbolError
from ..models import Candle, Instrument, Market, Quote, utcnow
from .base import MarketDataProvider

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exch}.json.gz"
CANDLES_URL = "https://api.upstox.com/v2/historical-candle/{key}/{interval}/{to}/{frm}"
INTRADAY_URL = "https://api.upstox.com/v2/historical-candle/intraday/{key}/{interval}"
INTERVAL_MAP = {"1m": "1minute", "30m": "30minute", "1d": "day", "1w": "week"}
IST = timezone(timedelta(hours=5, minutes=30))


class UpstoxData(MarketDataProvider):
    name = "upstox"
    markets = (Market.IN,)

    def __init__(self, settings=None):
        super().__init__(settings)
        cache_dir = Path(getattr(getattr(settings, "data", None), "cache_dir", "data/cache"))
        if settings is not None and not cache_dir.is_absolute():
            cache_dir = Path(settings.root) / cache_dir
        self.cache_dir = cache_dir
        self._instruments: dict[str, dict[str, dict]] = {}

    def _ping(self) -> str:
        key = self.instrument_key(Instrument(symbol="NSE:RELIANCE", market=Market.IN, base="RELIANCE", currency="INR", exchange="NSE"))
        c = self.candles(Instrument(symbol="NSE:RELIANCE", market=Market.IN, base="RELIANCE", currency="INR", exchange="NSE"), "1d", 1)
        return f"RELIANCE key={key} last close={c[-1].close if c else None}"

    def _load_instruments(self, exch: str) -> dict[str, dict]:
        if exch in self._instruments:
            return self._instruments[exch]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = self.cache_dir / f"upstox_{exch}.json.gz"
        if not cache.exists() or time.time() - cache.stat().st_mtime > 86_400:
            r = self.client.get(INSTRUMENTS_URL.format(exch=exch), timeout=60)
            if r.status_code != 200:
                raise DataError(f"upstox: instrument master HTTP {r.status_code}")
            cache.write_bytes(r.content)
        rows = json.loads(gzip.decompress(cache.read_bytes()))
        eq = {r["trading_symbol"].upper(): r for r in rows if r.get("segment") == f"{exch}_EQ"}
        idx = {r["trading_symbol"].upper(): r for r in rows if r.get("segment") == f"{exch}_INDEX"}
        eq.update({k: v for k, v in idx.items() if k not in eq})
        self._instruments[exch] = eq
        return eq

    def instrument_key(self, inst: Instrument) -> str:
        exch = inst.exchange or "NSE"
        table = self._load_instruments(exch)
        row = table.get(inst.base.upper())
        if not row:
            raise SymbolError(f"upstox: unknown {exch} symbol {inst.base}")
        return row["instrument_key"]

    def search(self, text: str, exch: str = "NSE", limit: int = 10) -> list[dict]:
        t = text.upper()
        table = self._load_instruments(exch)
        hits = [r for k, r in table.items() if t in k or t in (r.get("name") or "").upper()]
        hits.sort(key=lambda r: (not r["trading_symbol"].upper().startswith(t), r["trading_symbol"]))
        return [{"symbol": f"{exch}:{r['trading_symbol']}", "name": r.get("name"), "isin": r.get("isin"),
                 "instrument_key": r["instrument_key"], "lot_size": r.get("lot_size")} for r in hits[:limit]]

    def quote(self, inst: Instrument) -> Quote:
        # No public real time quote; derive from the latest intraday candle (may be stale after hours).
        c = self.candles(inst, "1d", 2)
        if not c:
            raise DataError(f"upstox: no candles for {inst.symbol}")
        last = c[-1]
        prev = c[-2].close if len(c) > 1 else None
        return Quote(symbol=inst.symbol, market=inst.market, currency="INR", last=last.close, open=last.open,
                     high=last.high, low=last.low, prev_close=prev, volume=last.volume, ts=last.ts, source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        if interval not in INTERVAL_MAP:
            raise DataError(f"upstox: unsupported interval {interval}; use one of {sorted(INTERVAL_MAP)}")
        key = self.instrument_key(inst).replace("|", "%7C")
        ui = INTERVAL_MAP[interval]
        end = end or utcnow()
        if start is None:
            per_day = {"1m": 375, "30m": 13, "1d": 1, "1w": 1 / 7}[interval]
            days = int(limit / per_day * 1.6) + 3
            start = end - timedelta(days=days)
        url = CANDLES_URL.format(key=key, interval=ui, to=end.astimezone(IST).strftime("%Y-%m-%d"),
                                 frm=start.astimezone(IST).strftime("%Y-%m-%d"))
        j = self._get_json(url)
        rows = (j.get("data") or {}).get("candles") or []
        if interval in ("1m", "30m"):
            # historical endpoint excludes today; add today's intraday candles
            try:
                j2 = self._get_json(INTRADAY_URL.format(key=key, interval=ui))
                rows = ((j2.get("data") or {}).get("candles") or []) + rows
            except DataError:
                pass
        out = [Candle(ts=datetime.fromisoformat(r[0]), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
        out.sort(key=lambda c: c.ts)
        return out[-limit:]
