"""Nasdaq.com public quote API for US equities (unofficial, no credentials).

Real time last sale for Nasdaq listed names, 15 minute delayed for others.
Daily history only. Use the Alpaca provider for intraday candles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote, utcnow
from .base import MarketDataProvider

BASE = "https://api.nasdaq.com/api/quote"


class NasdaqData(MarketDataProvider):
    name = "nasdaq"
    markets = (Market.US,)

    def _ping(self) -> str:
        j = self._get_json(f"{BASE}/AAPL/info", params={"assetclass": "stocks"})
        return f"AAPL last={j['data']['primaryData']['lastSalePrice']}"

    def quote(self, inst: Instrument) -> Quote:
        j = self._get_json(f"{BASE}/{inst.symbol}/info", params={"assetclass": "stocks"})
        d = j.get("data")
        if not d or not d.get("primaryData"):
            raise DataError(f"nasdaq: no data for {inst.symbol}", details=j.get("status", {}))
        p = d["primaryData"]
        sec = d.get("secondaryData") or {}
        last = self._f(p.get("lastSalePrice"))
        if last is None:
            raise DataError(f"nasdaq: no last price for {inst.symbol}")
        # After hours, primaryData is the extended session and secondaryData the regular close.
        reg_last = self._f(sec.get("lastSalePrice")) or last
        net = self._f(sec.get("netChange")) if sec.get("lastSalePrice") else self._f(p.get("netChange"))
        prev = reg_last - net if net is not None else None
        ks = d.get("keyStats") or {}
        high = low = None
        rng = (ks.get("dayrange") or {}).get("value") if isinstance(ks, dict) else None
        if rng and "-" in str(rng):
            lo, hi = str(rng).split("-", 1)
            low, high = self._f(lo), self._f(hi)
        return Quote(symbol=inst.symbol, market=inst.market, currency="USD", last=last,
                     bid=self._f(p.get("bidPrice")), ask=self._f(p.get("askPrice")), high=high, low=low,
                     volume=self._f(p.get("volume")) or self._f(sec.get("volume")), prev_close=prev, source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        if interval != "1d":
            raise DataError("nasdaq: only daily candles are available; configure alpaca for intraday")
        end = end or utcnow()
        start = start or end - timedelta(days=int(limit * 1.6) + 5)
        params = {"assetclass": "stocks", "fromdate": start.strftime("%Y-%m-%d"), "todate": end.strftime("%Y-%m-%d"), "limit": max(limit, 10)}
        j = self._get_json(f"{BASE}/{inst.symbol}/historical", params=params)
        rows = ((j.get("data") or {}).get("tradesTable") or {}).get("rows") or []
        out = []
        for r in rows:
            ts = datetime.strptime(r["date"], "%m/%d/%Y").replace(tzinfo=timezone.utc)
            out.append(Candle(ts=ts, open=self._f(r["open"]), high=self._f(r["high"]), low=self._f(r["low"]),
                              close=self._f(r["close"]), volume=self._f(r["volume"], 0.0)))
        out.sort(key=lambda c: c.ts)
        return out[-limit:]
