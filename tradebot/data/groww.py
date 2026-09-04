"""Groww public NSE quote endpoint (unofficial, no credentials). Quotes only."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote
from .base import MarketDataProvider

BASE = "https://groww.in/v1/api/stocks_data/v1/accord_points/exchange/{exch}/segment/CASH/latest_prices_ohlc/{sym}"


class GrowwData(MarketDataProvider):
    name = "groww"
    markets = (Market.IN,)

    def _ping(self) -> str:
        j = self._get_json(BASE.format(exch="NSE", sym="RELIANCE"))
        return f"RELIANCE ltp={j.get('ltp')}"

    def quote(self, inst: Instrument) -> Quote:
        j = self._get_json(BASE.format(exch=inst.exchange or "NSE", sym=inst.base))
        if not j or j.get("ltp") in (None, 0):
            raise DataError(f"groww: no quote for {inst.symbol}")
        return Quote(symbol=inst.symbol, market=inst.market, currency="INR", last=float(j["ltp"]),
                     open=self._f(j.get("open")), high=self._f(j.get("high")), low=self._f(j.get("low")),
                     prev_close=self._f(j.get("close")), volume=self._f(j.get("volume")), source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        raise DataError("groww: candles not supported; upstox provider handles NSE candles")
