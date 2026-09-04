from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from tradebot.config import Settings
from tradebot.data.base import MarketDataProvider
from tradebot.data.registry import MarketData
from tradebot.engine import TradingEngine
from tradebot.models import Candle, Instrument, Market, Quote
from tradebot.store import Store


class FakeProvider(MarketDataProvider):
    """Deterministic quotes for tests; prices are set per symbol via ``prices``."""

    name = "fake"
    markets = (Market.US, Market.IN, Market.CRYPTO)

    def __init__(self, settings=None):
        super().__init__(settings)
        self.prices: dict[str, float] = {"BTC-USD": 50_000.0, "AAPL": 200.0, "NSE:RELIANCE": 1_300.0, "ETH-USD": 2_500.0}
        self.spread_bps = 10.0

    def quote(self, inst: Instrument) -> Quote:
        px = self.prices[inst.symbol]
        half = px * self.spread_bps / 20_000
        return Quote(symbol=inst.symbol, market=inst.market, currency=inst.currency, last=px, bid=px - half, ask=px + half,
                     prev_close=px * 0.99, source=self.name)

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100, start: Optional[datetime] = None,
                end: Optional[datetime] = None) -> list[Candle]:
        px = self.prices[inst.symbol]
        now = datetime.now(timezone.utc)
        return [Candle(ts=now - timedelta(days=limit - i), open=px, high=px * 1.01, low=px * 0.99, close=px, volume=1000) for i in range(limit)]


class FakeMarketData(MarketData):
    def __init__(self, settings):
        super().__init__(settings)
        self.fake = FakeProvider(settings)

    def providers_for(self, market, want_candles=False):
        return [self.fake]

    def provider(self, name):
        return self.fake


@pytest.fixture
def settings(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    s.root = str(tmp_path)
    s.risk.kill_switch_file = str(tmp_path / "KILL")
    s.paper.slippage_bps = 0.0
    s.paper.fee_bps = {"us": 0.0, "in": 0.0, "crypto": 0.0}
    s.data.quote_ttl_seconds = 0.0
    return s


@pytest.fixture
def engine(settings):
    store = Store(settings.resolve(settings.db_path))
    data = FakeMarketData(settings)
    return TradingEngine(settings, store, data)


@pytest.fixture
def prices(engine):
    return engine.data.fake.prices
