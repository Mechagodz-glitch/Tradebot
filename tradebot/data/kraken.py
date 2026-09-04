"""Kraken public market data (no credentials)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote
from .base import MarketDataProvider

BASE = "https://api.kraken.com/0/public"
INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
ALIAS = {"BTC": "XBT", "DOGE": "XDG"}


class KrakenData(MarketDataProvider):
    name = "kraken"
    markets = (Market.CRYPTO,)

    @staticmethod
    def _pair(inst: Instrument) -> str:
        return f"{ALIAS.get(inst.base, inst.base)}{inst.currency}"

    def _ping(self) -> str:
        j = self._get_json(f"{BASE}/Time")
        return f"server time {j['result']['rfc1123']}"

    def _result(self, j: dict) -> dict:
        if j.get("error"):
            raise DataError(f"kraken: {j['error']}")
        return j["result"]

    def quote(self, inst: Instrument) -> Quote:
        pair = self._pair(inst)
        res = self._result(self._get_json(f"{BASE}/Ticker", params={"pair": pair}))
        t = next(iter(res.values()))
        return Quote(symbol=inst.symbol, market=inst.market, currency=inst.currency,
                     last=float(t["c"][0]), bid=float(t["b"][0]), ask=float(t["a"][0]),
                     open=float(t["o"]), high=float(t["h"][1]), low=float(t["l"][1]),
                     volume=float(t["v"][1]), prev_close=float(t["o"]), source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        if interval not in INTERVAL_MIN:
            raise DataError(f"kraken: unsupported interval {interval}")
        params = {"pair": self._pair(inst), "interval": INTERVAL_MIN[interval]}
        if start:
            params["since"] = int(start.timestamp())
        res = self._result(self._get_json(f"{BASE}/OHLC", params=params))
        rows = next(v for k, v in res.items() if k != "last")
        out = [Candle(ts=datetime.fromtimestamp(r[0], tz=timezone.utc), open=float(r[1]), high=float(r[2]),
                      low=float(r[3]), close=float(r[4]), volume=float(r[6])) for r in rows]
        if end:
            out = [c for c in out if c.ts <= end]
        return out[-limit:]
