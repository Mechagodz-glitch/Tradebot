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


class SoldHoldingKite(FakeKite):
    """State after selling a T1 holding: holdings row at qty 0, a -5 CNC net position, funds unchanged."""

    def margins(self, segment):
        return {"net": 3804.6, "available": {"cash": 3804.6}, "utilised": {"debits": 0}}

    def holdings(self):
        return [
            {"exchange": "BSE", "tradingsymbol": "GICRE", "quantity": 0, "t1_quantity": 0, "average_price": 351.6, "last_price": 347.7, "pnl": 0},
            {"exchange": "NSE", "tradingsymbol": "OIL", "quantity": 0, "t1_quantity": 4, "average_price": 483.95, "last_price": 498.3, "pnl": 57.4},
            {"exchange": "BSE", "tradingsymbol": "SWIGGY", "quantity": 0, "t1_quantity": 9, "average_price": 277.15, "last_price": 276.0, "pnl": -10.35},
        ]

    def positions(self):
        return {"net": [{"exchange": "NSE", "tradingsymbol": "GICRE", "quantity": -5, "product": "CNC", "average_price": 347.5,
                         "sell_value": 1737.5, "buy_value": 0, "last_price": 347.7, "pnl": -1}], "day": []}


def test_sold_holding_is_not_a_short_and_proceeds_count_as_cash(engine, monkeypatch):
    b = _broker(engine)
    b._kite = SoldHoldingKite()
    monkeypatch.setattr(b, "_canon_exchange", lambda exch, sym: "NSE")
    pos = {p.symbol: p for p in b.positions(Market.IN, mark=False)}
    assert set(pos) == {"NSE:OIL", "NSE:SWIGGY"}          # no phantom -5 GICRE
    a = b.account(Market.IN)
    assert a.cash == 3804.6 + 1737.5
    assert a.equity == a.cash + 4 * 498.3 + 9 * 276.0     # ~10,019, not ~6,543
