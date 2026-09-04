import pytest

from tradebot.brokers.paper import apply_fill
from tradebot.errors import NotFound, RiskRejected
from tradebot.models import Market, OrderRequest, OrderStatus, OrderType, Side, TimeInForce


def test_apply_fill_accounting():
    q, avg, r = apply_fill(0, 0, Side.BUY, 10, 100)
    assert (q, avg, r) == (10, 100, 0)
    q, avg, r = apply_fill(q, avg, Side.BUY, 10, 120)
    assert (q, avg, r) == (20, 110, 0)
    q, avg, r = apply_fill(q, avg, Side.SELL, 5, 130)
    assert (q, avg, r) == (15, 110, 100)
    q, avg, r = apply_fill(q, avg, Side.SELL, 15, 100)
    assert (q, avg, r) == (0, 0, -150)
    # flip long -> short
    q, avg, r = apply_fill(5, 100, Side.SELL, 8, 110)
    assert (q, avg, r) == (-3, 110, 50)


def test_market_buy_and_sell_roundtrip(engine, prices):
    o = engine.place_order(OrderRequest(symbol="BTC-USD", side=Side.BUY, qty=0.05, reason="test entry"))
    assert o.status == OrderStatus.FILLED
    assert o.avg_fill_price == pytest.approx(50_000 * (1 + 0.0005))  # filled at ask (10bps spread)
    acct = engine.account(Market.CRYPTO)
    assert acct.cash == pytest.approx(100_000 - 0.05 * o.avg_fill_price)
    pos = engine.positions(market=Market.CRYPTO)
    assert len(pos) == 1 and pos[0].qty == pytest.approx(0.05)

    prices["BTC-USD"] = 60_000.0
    pos = engine.positions(market=Market.CRYPTO)[0]
    assert pos.unrealized_pnl == pytest.approx((60_000 - o.avg_fill_price) * 0.05)

    c = engine.close_position("BTC-USD")
    assert c.status == OrderStatus.FILLED and c.side == Side.SELL
    assert engine.positions(market=Market.CRYPTO) == []
    pnl = engine.pnl(market=Market.CRYPTO)[0]
    assert pnl["realized_pnl"] > 0 and pnl["total_pnl"] == pytest.approx(pnl["realized_pnl"])
    kinds = [j.kind for j in engine.journal(limit=20)]
    assert "fill" in kinds and "order" in kinds


def test_limit_order_rests_then_fills_on_sync(engine, prices):
    o = engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=10, order_type=OrderType.LIMIT, limit_price=190, tif=TimeInForce.GTC))
    assert o.status == OrderStatus.ACCEPTED
    assert engine.orders(open_only=True)[0].id == o.id
    prices["AAPL"] = 185.0
    res = engine.sync("paper")
    assert res["paper"]["filled"] == 1
    o2 = engine.order(o.id)
    assert o2.status == OrderStatus.FILLED and o2.avg_fill_price <= 190
    assert res["paper"]["equity"]["us"] == pytest.approx(100_000 - 10 * o2.avg_fill_price + 10 * 185.0)


def test_stop_order_triggers(engine, prices):
    engine.place_order(OrderRequest(symbol="ETH-USD", side=Side.BUY, qty=1))
    stop = engine.place_order(OrderRequest(symbol="ETH-USD", side=Side.SELL, qty=1, order_type=OrderType.STOP, stop_price=2_400, tif=TimeInForce.GTC))
    assert stop.status == OrderStatus.ACCEPTED
    prices["ETH-USD"] = 2_390.0
    engine.sync("paper")
    assert engine.order(stop.id).status == OrderStatus.FILLED
    assert engine.positions(market=Market.CRYPTO) == []


def test_ioc_not_marketable_is_canceled(engine):
    o = engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1, order_type=OrderType.LIMIT, limit_price=100, tif=TimeInForce.IOC))
    assert o.status == OrderStatus.CANCELED


def test_insufficient_funds_and_position(engine, settings):
    settings.risk.max_order_notional = {"USD": 10_000_000, "INR": 10_000_000}
    settings.risk.max_position_notional = {"USD": 10_000_000, "INR": 10_000_000}
    o = engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1_000, reason="too big"))
    assert o.status == OrderStatus.REJECTED and "insufficient_funds" in o.reject_reason
    o = engine.place_order(OrderRequest(symbol="AAPL", side=Side.SELL, qty=1))
    assert o.status == OrderStatus.REJECTED and "insufficient_position" in o.reject_reason


def test_cancel_and_cancel_all(engine):
    o = engine.place_order(OrderRequest(symbol="NSE:RELIANCE", side=Side.BUY, qty=5, order_type=OrderType.LIMIT, limit_price=1_000))
    assert o.status == OrderStatus.ACCEPTED
    c = engine.cancel_order(o.id)
    assert c.status == OrderStatus.CANCELED
    engine.place_order(OrderRequest(symbol="NSE:RELIANCE", side=Side.BUY, qty=5, order_type=OrderType.LIMIT, limit_price=1_000))
    engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=5, order_type=OrderType.LIMIT, limit_price=100))
    assert len(engine.cancel_all()) == 2
    assert engine.orders(open_only=True) == []


def test_close_without_position(engine):
    with pytest.raises(NotFound):
        engine.close_position("AAPL")


def test_dry_run_does_not_trade(engine):
    o = engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1), dry_run=True)
    assert o.id == "dry-run" and "dry_run ok" in o.reject_reason
    assert engine.orders() == []


def test_reset_paper(engine):
    engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1))
    engine.brokers.paper.reset()
    assert engine.orders() == [] and engine.positions() == []
    assert engine.account(Market.US).cash == 100_000
