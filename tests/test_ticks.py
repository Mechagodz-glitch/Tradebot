import pytest

from tradebot.models import Market, Side
from tradebot.ticks import round_to_tick


def test_nse_ticks_round_up_for_buys_and_down_for_sells():
    assert round_to_tick(277.57, Market.IN, Side.BUY) == 277.60
    assert round_to_tick(277.57, Market.IN, Side.SELL) == 277.55
    assert round_to_tick(277.575, Market.IN) == 277.60
    assert round_to_tick(351.75, Market.IN, Side.BUY) == 351.75   # already on tick
    assert round_to_tick(484.83, Market.IN, Side.BUY) == 484.85


def test_us_and_crypto_ticks():
    assert round_to_tick(319.777, Market.US, Side.BUY) == 319.78
    assert round_to_tick(79826.0234, Market.CRYPTO, Side.SELL) == 79826.02


def test_thesis_entry_limit_is_on_tick(engine, settings, prices):
    from tradebot.models import ThesisRequest
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    prices["NSE:SWIGGY"] = 277.15
    t = engine.open_thesis(ThesisRequest(symbol="NSE:SWIGGY", text="x", size_notional=2_500), execute=True)
    o = engine.order(t.entry_order_id)
    assert o.limit_price == pytest.approx(277.60) and (round(o.limit_price * 100) % 5) == 0
