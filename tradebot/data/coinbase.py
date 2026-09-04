"""Coinbase Exchange public market data (no credentials)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote, utcnow
from .base import MarketDataProvider

BASE = "https://api.exchange.coinbase.com"
GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 21600, "6h": 21600, "1d": 86400}


class CoinbaseData(MarketDataProvider):
    name = "coinbase"
    markets = (Market.CRYPTO,)

    def _ping(self) -> str:
        j = self._get_json(f"{BASE}/products/BTC-USD/ticker")
        return f"BTC-USD last={j.get('price')}"

    def quote(self, inst: Instrument) -> Quote:
        pid = inst.symbol  # BTC-USD is Coinbase's own format
        t = self._get_json(f"{BASE}/products/{pid}/ticker")
        if "message" in t:
            raise DataError(f"coinbase: {t['message']} ({pid})")
        s = self._get_json(f"{BASE}/products/{pid}/stats")
        return Quote(symbol=inst.symbol, market=inst.market, currency=inst.currency,
                     last=self._f(t["price"]), bid=self._f(t.get("bid")), ask=self._f(t.get("ask")),
                     open=self._f(s.get("open")), high=self._f(s.get("high")), low=self._f(s.get("low")),
                     volume=self._f(s.get("volume")), prev_close=self._f(s.get("open")), source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        if interval not in GRANULARITY:
            raise DataError(f"coinbase: unsupported interval {interval}; use one of {sorted(GRANULARITY)}")
        g = GRANULARITY[interval]
        end = end or utcnow()
        start = start or end - timedelta(seconds=g * min(limit, 300))
        params = {"granularity": g, "start": start.isoformat(), "end": end.isoformat()}
        rows = self._get_json(f"{BASE}/products/{inst.symbol}/candles", params=params)
        if isinstance(rows, dict):
            raise DataError(f"coinbase: {rows.get('message')}")
        out = [Candle(ts=datetime.fromtimestamp(r[0], tz=timezone.utc), low=r[1], high=r[2], open=r[3], close=r[4], volume=r[5])
               for r in rows]
        out.sort(key=lambda c: c.ts)
        return out[-limit:]
