"""Zerodha Kite Connect market data (needs KITE_API_KEY and a daily KITE_ACCESS_TOKEN).

Real time NSE/BSE quotes with best bid/ask from market depth. Candles use the historical data API,
which needs the historical data add-on on the Kite Connect app; without it the registry falls
through to the Upstox provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote, utcnow
from .base import MarketDataProvider

INTERVALS = {"1m": "minute", "5m": "5minute", "15m": "15minute", "30m": "30minute", "1h": "60minute", "1d": "day"}
BARS_PER_DAY = {"1m": 375, "5m": 75, "15m": 25, "30m": 13, "1h": 7, "1d": 1}
IST = timezone(timedelta(hours=5, minutes=30))


class KiteData(MarketDataProvider):
    name = "kite"
    markets = (Market.IN,)
    requires_credentials = True

    def __init__(self, settings=None):
        super().__init__(settings)
        self._kite = None
        self._tokens: dict[str, int] = {}

    def available(self) -> bool:
        return bool(self.settings and self.settings.kite_api_key and self.settings.kite_access_token)

    @property
    def kite(self):
        if self._kite is None:
            from kiteconnect import KiteConnect
            if not self.available():
                raise DataError("kite: KITE_API_KEY / KITE_ACCESS_TOKEN not set")
            k = KiteConnect(api_key=self.settings.kite_api_key)
            k.set_access_token(self.settings.kite_access_token)
            self._kite = k
        return self._kite

    @staticmethod
    def _key(inst: Instrument) -> str:
        return f"{inst.exchange or 'NSE'}:{inst.base}"

    @staticmethod
    def _ts(v) -> datetime:
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=IST)
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v).replace(tzinfo=IST)
            except ValueError:
                pass
        return utcnow()

    def _ping(self) -> str:
        q = self.quote(Instrument(symbol="NSE:RELIANCE", market=Market.IN, base="RELIANCE", currency="INR", exchange="NSE"))
        return f"RELIANCE last={q.last} bid={q.bid} ask={q.ask}"

    def quote(self, inst: Instrument) -> Quote:
        key = self._key(inst)
        try:
            data = self.kite.quote([key])
        except Exception as e:  # noqa: BLE001
            raise DataError(f"kite: {e}") from e
        d = data.get(key)
        if not d:
            raise DataError(f"kite: no quote for {inst.symbol}")
        if d.get("instrument_token"):
            self._tokens[key] = int(d["instrument_token"])
        depth = d.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0]
        sell = (depth.get("sell") or [{}])[0]
        ohlc = d.get("ohlc") or {}
        return Quote(symbol=inst.symbol, market=Market.IN, currency="INR", last=float(d["last_price"]),
                     bid=self._f(buy.get("price")) or None, ask=self._f(sell.get("price")) or None,
                     open=self._f(ohlc.get("open")), high=self._f(ohlc.get("high")), low=self._f(ohlc.get("low")),
                     prev_close=self._f(ohlc.get("close")), volume=self._f(d.get("volume")),
                     ts=self._ts(d.get("last_trade_time") or d.get("timestamp")), source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        if interval not in INTERVALS:
            raise DataError(f"kite: unsupported interval {interval}; use one of {sorted(INTERVALS)}")
        key = self._key(inst)
        if key not in self._tokens:
            self.quote(inst)
        token = self._tokens.get(key)
        if not token:
            raise DataError(f"kite: could not resolve instrument token for {inst.symbol}")
        end = end or utcnow()
        if start is None:
            start = end - timedelta(days=int(limit / BARS_PER_DAY[interval] * 1.6) + 3)
        try:
            rows = self.kite.historical_data(token, start.astimezone(IST).replace(tzinfo=None),
                                             end.astimezone(IST).replace(tzinfo=None), INTERVALS[interval])
        except Exception as e:  # noqa: BLE001
            raise DataError(f"kite: {e}") from e
        out = [Candle(ts=self._ts(r["date"]), open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                      volume=r.get("volume") or 0.0) for r in rows]
        out.sort(key=lambda c: c.ts)
        return out[-limit:]
