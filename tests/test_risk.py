import pytest

from tradebot.errors import BrokerError, RiskRejected
from tradebot.models import EquityPoint, Market, OrderRequest, OrderType, Side, utcnow
from datetime import timedelta


def place(engine, **kw):
    kw.setdefault("side", Side.BUY)
    return engine.place_order(OrderRequest(**kw))


def test_kill_switch(engine):
    engine.set_kill_switch(True)
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=1)
    assert e.value.code == "kill_switch"
    engine.set_kill_switch(False)
    assert place(engine, symbol="AAPL", qty=1).status.value == "filled"
    assert any(j.kind == "risk" for j in engine.journal())


def test_order_too_large(engine):
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="BTC-USD", qty=1)  # 50k > 5k limit
    assert e.value.code == "order_too_large"


def test_position_too_large_but_reducing_allowed(engine, settings):
    settings.risk.max_order_notional["USD"] = 100_000
    settings.risk.max_position_notional["USD"] = 15_000
    place(engine, symbol="AAPL", qty=50)  # 10k
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=50)  # would be 20k
    assert e.value.code == "position_too_large"
    assert place(engine, symbol="AAPL", qty=20, side=Side.SELL).status.value == "filled"


def test_market_and_symbol_filters(engine, settings):
    settings.risk.allowed_markets = ["crypto"]
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=1)
    assert e.value.code == "market_not_allowed"
    settings.risk.allowed_markets = ["us", "in", "crypto"]
    settings.risk.blocked_symbols = ["AAPL"]
    with pytest.raises(RiskRejected):
        place(engine, symbol="AAPL", qty=1)
    settings.risk.blocked_symbols = []
    settings.risk.allowed_symbols = ["ETH-USD"]
    with pytest.raises(RiskRejected):
        place(engine, symbol="AAPL", qty=1)
    assert place(engine, symbol="ETH-USD", qty=0.1).status.value == "filled"


def test_open_order_and_rate_limits(engine, settings):
    settings.risk.max_open_orders = 2
    for _ in range(2):
        place(engine, symbol="AAPL", qty=1, order_type=OrderType.LIMIT, limit_price=100)
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=1, order_type=OrderType.LIMIT, limit_price=100)
    assert e.value.code == "too_many_open_orders"
    engine.cancel_all()
    settings.risk.max_orders_per_minute = 3  # 2 stored orders so far; risk-rejected ones are not stored
    place(engine, symbol="AAPL", qty=1)
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=1)
    assert e.value.code == "rate_limited"


def test_daily_loss_limit(engine, settings, prices):
    settings.risk.max_daily_loss["USD"] = 500
    engine.store.add_equity_point(EquityPoint(venue="paper", market=Market.US, ts=utcnow() - timedelta(days=1),
                                              cash=100_000, positions_value=0, equity=100_000))
    place(engine, symbol="AAPL", qty=20)  # 4k position
    prices["AAPL"] = 170.0  # -600 unrealized
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=1)
    assert e.value.code == "daily_loss_limit"
    # reducing is still allowed
    assert place(engine, symbol="AAPL", qty=20, side=Side.SELL).status.value == "filled"


def test_live_venue_gate(engine, settings):
    settings.alpaca_api_key = "k"
    settings.alpaca_secret_key = "s"
    settings.alpaca.paper = False
    with pytest.raises(RiskRejected) as e:
        place(engine, symbol="AAPL", qty=1, venue="alpaca")
    assert e.value.code == "live_trading_disabled"


def test_unconfigured_venue(engine):
    with pytest.raises(BrokerError) as e:
        place(engine, symbol="NSE:RELIANCE", qty=1, venue="kite")
    assert e.value.code == "credentials_missing"


def test_unsupported_market_on_venue(engine, settings):
    settings.kite_api_key = "k"
    settings.kite_access_token = "t"
    with pytest.raises(BrokerError) as e:
        place(engine, symbol="AAPL", qty=1, venue="kite")
    assert e.value.code == "market_unsupported"
