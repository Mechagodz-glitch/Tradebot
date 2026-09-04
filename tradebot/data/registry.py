"""Resolves a market to an ordered list of providers and applies fallback + a short quote cache."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from ..errors import DataError
from ..models import Candle, CheckResult, Instrument, Market, Quote
from .alpaca_data import AlpacaData
from .base import MarketDataProvider
from .ccxt_data import CcxtData
from .coinbase import CoinbaseData
from .groww import GrowwData
from .kite_data import KiteData
from .kraken import KrakenData
from .nasdaq import NasdaqData
from .upstox import UpstoxData

PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "coinbase": CoinbaseData,
    "kraken": KrakenData,
    "ccxt": CcxtData,
    "nasdaq": NasdaqData,
    "alpaca": AlpacaData,
    "kite": KiteData,
    "groww": GrowwData,
    "upstox": UpstoxData,
}


class MarketData:
    def __init__(self, settings):
        self.settings = settings
        self._providers: dict[str, MarketDataProvider] = {}
        self._quote_cache: dict[str, tuple[float, Quote]] = {}

    def provider(self, name: str) -> MarketDataProvider:
        if name not in PROVIDERS:
            raise DataError(f"unknown data provider {name}; known: {sorted(PROVIDERS)}")
        if name not in self._providers:
            self._providers[name] = PROVIDERS[name](self.settings)
        return self._providers[name]

    def providers_for(self, market: Market, want_candles: bool = False) -> list[MarketDataProvider]:
        out = []
        for name in self.settings.data.providers_for(market):
            p = self.provider(name)
            if market in p.markets and p.available():
                if want_candles and name == "groww":
                    continue
                out.append(p)
        return out

    def quote(self, inst: Instrument, provider: Optional[str] = None, use_cache: bool = True) -> Quote:
        ttl = self.settings.data.quote_ttl_seconds
        key = f"{inst.symbol}|{provider or ''}"
        if use_cache and key in self._quote_cache:
            ts, q = self._quote_cache[key]
            if time.time() - ts < ttl:
                return q
        providers = [self.provider(provider)] if provider else self.providers_for(inst.market)
        if not providers:
            raise DataError(f"no data provider available for market {inst.market.value}")
        errors = []
        for p in providers:
            try:
                q = p.quote(inst)
                self._quote_cache[key] = (time.time(), q)
                return q
            except Exception as e:  # noqa: BLE001
                errors.append(f"{p.name}: {e}")
        raise DataError(f"all providers failed for {inst.symbol}", details={"errors": errors})

    def quote_many(self, insts: list[Instrument]) -> dict[str, Quote]:
        """Quotes for many instruments of one market: batch through the first provider, fall back per symbol."""
        if not insts:
            return {}
        market = insts[0].market
        out: dict[str, Quote] = {}
        for p in self.providers_for(market):
            missing = [i for i in insts if i.symbol not in out]
            if not missing:
                break
            try:
                out.update(p.quote_many(missing))
            except Exception:  # noqa: BLE001
                continue
        now = time.time()
        for sym, q in out.items():
            self._quote_cache[f"{sym}|"] = (now, q)
        return out

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100, start: Optional[datetime] = None,
                end: Optional[datetime] = None, provider: Optional[str] = None) -> tuple[list[Candle], str]:
        providers = [self.provider(provider)] if provider else self.providers_for(inst.market, want_candles=True)
        if not providers:
            raise DataError(f"no candle provider available for market {inst.market.value}")
        errors = []
        for p in providers:
            try:
                return p.candles(inst, interval=interval, limit=limit, start=start, end=end), p.name
            except Exception as e:  # noqa: BLE001
                errors.append(f"{p.name}: {e}")
        raise DataError(f"all candle providers failed for {inst.symbol}", details={"errors": errors})

    def check_all(self) -> list[CheckResult]:
        out = []
        for name in PROVIDERS:
            p = self.provider(name)
            if not p.available():
                out.append(CheckResult(name=f"data:{name}", ok=False, detail="credentials not configured (skipped)"))
                continue
            ok, detail, ms = p.ping()
            out.append(CheckResult(name=f"data:{name}", ok=ok, detail=detail, latency_ms=ms))
        return out
