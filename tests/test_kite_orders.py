from tradebot.brokers.kite import KiteBroker
from tradebot.config import Settings
from tradebot.models import Market, OrderRequest, OrderType, Side
from tradebot.symbols import parse_symbol


class FakeKite:
    VARIETY_REGULAR = "regular"; ORDER_TYPE_MARKET = "MARKET"; ORDER_TYPE_LIMIT = "LIMIT"; ORDER_TYPE_SLM = "SL-M"; ORDER_TYPE_SL = "SL"
    VALIDITY_IOC = "IOC"; VALIDITY_DAY = "DAY"; TRANSACTION_TYPE_BUY = "BUY"; TRANSACTION_TYPE_SELL = "SELL"

    def __init__(self):
        self.calls = []

    def place_order(self, **kw):
        self.calls.append(kw)
        return "260908000001"

    def order_history(self, oid):
        return [{"status": "COMPLETE", "filled_quantity": 5, "average_price": 347.2}]


def _broker(engine):
    s = engine.settings
    s.kite_api_key, s.kite_access_token = "k", "t"
    b = KiteBroker(s, engine.store, engine.data)
    b._kite = FakeKite()
    return b


def test_market_and_stop_orders_request_market_protection(engine):
    b = _broker(engine)
    inst = parse_symbol("NSE:GICRE")
    o = b.place_order(OrderRequest(symbol="NSE:GICRE", side=Side.SELL, qty=5, venue="kite"), inst)
    kw = b._kite.calls[-1]
    assert kw["order_type"] == "MARKET" and kw["market_protection"] == -1 and kw["price"] is None
    assert o.status.value == "filled" and o.avg_fill_price == 347.2
    b.place_order(OrderRequest(symbol="NSE:GICRE", side=Side.SELL, qty=5, venue="kite", order_type=OrderType.STOP, stop_price=340.0), inst)
    kw = b._kite.calls[-1]
    assert kw["order_type"] == "SL-M" and kw["market_protection"] == -1 and kw["trigger_price"] == 340.0


def test_limit_orders_have_no_protection_and_tick_rounding(engine):
    b = _broker(engine)
    inst = parse_symbol("NSE:GESHIP")
    b.place_order(OrderRequest(symbol="NSE:GESHIP", side=Side.BUY, qty=2, venue="kite", order_type=OrderType.LIMIT, limit_price=1382.27), inst)
    kw = b._kite.calls[-1]
    assert kw["order_type"] == "LIMIT" and kw["market_protection"] is None and kw["price"] == 1382.30
