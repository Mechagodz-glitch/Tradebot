"""Alpaca market data (needs ALPACA_API_KEY / ALPACA_SECRET_KEY; free with a paper account).
Covers US equities (IEX feed on the free plan) and crypto."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote, utcnow
from .base import MarketDataProvider

TF = {"1m": ("1", "Min"), "5m": ("5", "Min"), "15m": ("15", "Min"), "30m": ("30", "Min"), "1h": ("1", "Hour"),
      "4h": ("4", "Hour"), "1d": ("1", "Day"), "1w": ("1", "Week")}


class AlpacaData(MarketDataProvider):
    name = "alpaca"
    markets = (Market.US, Market.CRYPTO)
    requires_credentials = True

    def available(self) -> bool:
        return bool(self.settings and self.settings.alpaca_api_key and self.settings.alpaca_secret_key)

    def _clients(self):
        from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
        if not self.available():
            raise DataError("alpaca: ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        k, s = self.settings.alpaca_api_key, self.settings.alpaca_secret_key
        return StockHistoricalDataClient(k, s), CryptoHistoricalDataClient(k, s)

    @staticmethod
    def _sym(inst: Instrument) -> str:
        return f"{inst.base}/{inst.currency}" if inst.market == Market.CRYPTO else inst.symbol

    def _ping(self) -> str:
        q = self.quote(Instrument(symbol="AAPL", market=Market.US, base="AAPL", currency="USD"))
        return f"AAPL last={q.last}"

    def quote(self, inst: Instrument) -> Quote:
        from alpaca.data.requests import CryptoSnapshotRequest, StockSnapshotRequest
        stock, crypto = self._clients()
        sym = self._sym(inst)
        if inst.market == Market.CRYPTO:
            snap = crypto.get_crypto_snapshot(CryptoSnapshotRequest(symbol_or_symbols=sym))[sym]
        else:
            snap = stock.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=sym, feed="iex"))[sym]
        t, q, day, prev = snap.latest_trade, snap.latest_quote, snap.daily_bar, snap.previous_daily_bar
        if t is None:
            raise DataError(f"alpaca: no trade data for {inst.symbol}")
        return Quote(symbol=inst.symbol, market=inst.market, currency=inst.currency, last=float(t.price),
                     bid=(float(q.bid_price) or None) if q else None, ask=(float(q.ask_price) or None) if q else None,
                     open=float(day.open) if day else None, high=float(day.high) if day else None,
                     low=float(day.low) if day else None, volume=float(day.volume) if day else None,
                     prev_close=float(prev.close) if prev else None, ts=t.timestamp, source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        if interval not in TF:
            raise DataError(f"alpaca: unsupported interval {interval}")
        n, unit = TF[interval]
        tf = TimeFrame(int(n), TimeFrameUnit(unit))
        stock, crypto = self._clients()
        sym = self._sym(inst)
        end = end or utcnow()
        if start is None:
            seconds_per_bar = {"Min": 60, "Hour": 3600, "Day": 86400, "Week": 604800}[unit] * int(n)
            span = timedelta(seconds=seconds_per_bar * limit)
            if inst.market == Market.US:
                # Equities trade ~6.5h on weekdays and IEX extended-hours bars are sparse: widen the window.
                start = end - max(span * 4, timedelta(days=4))
            else:
                start = end - span * 1.5
        # Alpaca's `limit` returns the OLDEST bars in the window, so fetch the window (alpaca-py paginates)
        # and keep the newest `limit` locally.
        if inst.market == Market.CRYPTO:
            bars = crypto.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=sym, timeframe=tf, start=start, end=end))
        else:
            bars = stock.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=tf, start=start, end=end, feed="iex"))
        rows = bars.data.get(sym, [])
        out = [Candle(ts=b.timestamp, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume) for b in rows]
        out.sort(key=lambda c: c.ts)
        return out[-limit:]
