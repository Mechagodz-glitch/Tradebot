from datetime import datetime, timedelta, timezone

from tradebot.models import Candle, Market, OrderRequest, Side
from tradebot.strategy import TrendStrategy


def make_series(fake, symbol, closes):
    """Install a deterministic candle series (and set the quote to the last close)."""
    now = datetime.now(timezone.utc)
    n = len(closes)
    fake.series = getattr(fake, "series", {})
    fake.series[symbol] = [Candle(ts=now - timedelta(days=n - i), open=c, high=c, low=c, close=c, volume=1) for i, c in enumerate(closes)]
    fake.prices[symbol] = closes[-1]
    orig = fake.candles

    def candles(inst, interval="1d", limit=100, start=None, end=None):
        if inst.symbol in fake.series:
            return fake.series[inst.symbol][-limit:]
        return orig(inst, interval, limit, start, end)
    fake.candles = candles


def test_plan_buys_top_momentum_and_respects_slots(engine, settings, prices):
    settings.strategy.universe = {"in": ["NSE:AAA", "NSE:BBB", "NSE:CCC"]}
    settings.strategy.max_positions = 2
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    fake = engine.data.fake
    make_series(fake, "NSE:AAA", [100 + i * 1.0 for i in range(60)])      # strong uptrend, last 159
    make_series(fake, "NSE:BBB", [200 + i * 0.5 for i in range(60)])      # mild uptrend, last 229.5
    make_series(fake, "NSE:CCC", [300 - i * 1.0 for i in range(60)])      # downtrend
    plan = TrendStrategy(engine).plan(Market.IN, "paper")
    buys = [o for o in plan.orders if o.side == Side.BUY]
    assert [o.symbol for o in buys] == ["NSE:AAA", "NSE:BBB"]           # ranked by momentum, CCC excluded
    for o in buys:
        assert o.order_type.value == "limit" and o.qty == int(o.qty) and o.qty >= 1
        assert o.notional <= 10_000 * 0.30 * 1.01 and o.notional <= 4_000
    assert any("uptrend" in n for n in plan.notes) is False


def test_execute_then_exit_on_stop_and_trend_break(engine, settings, prices):
    settings.strategy.universe = {"in": ["NSE:AAA"]}
    settings.strategy.max_positions = 1
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    fake = engine.data.fake
    make_series(fake, "NSE:AAA", [100 + i for i in range(60)])
    strat = TrendStrategy(engine)
    plan = strat.plan(Market.IN, "paper")
    res = strat.execute(plan)
    assert res and res[0]["status"] == "filled"
    held = engine.positions("paper", Market.IN)[0]
    assert held.qty >= 1
    # price collapses below the stop: plan must propose a full exit and nothing else
    fake.prices["NSE:AAA"] = held.avg_price * 0.95
    plan2 = strat.plan(Market.IN, "paper")
    assert len(plan2.orders) == 1 and plan2.orders[0].side == Side.SELL and plan2.orders[0].qty == held.qty
    assert "stop loss" in plan2.orders[0].reason
    strat.execute(plan2)
    assert engine.positions("paper", Market.IN) == []


def test_dry_run_sends_nothing(engine, settings):
    settings.strategy.universe = {"in": ["NSE:AAA"]}
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    make_series(engine.data.fake, "NSE:AAA", [100 + i for i in range(60)])
    strat = TrendStrategy(engine)
    res = strat.execute(strat.plan(Market.IN, "paper"), dry_run=True)
    assert res[0]["status"] == "new" and "dry_run ok" in res[0]["detail"]
    assert engine.orders() == []


def test_insufficient_budget_note(engine, settings):
    settings.strategy.universe = {"in": ["NSE:AAA"]}
    settings.paper.starting_cash["in"] = 10_000
    make_series(engine.data.fake, "NSE:AAA", [5000 + i * 50 for i in range(60)])   # trending, but one share costs more than the budget
    plan = TrendStrategy(engine).plan(Market.IN, "paper")
    assert plan.orders == [] and any("less than one unit" in n for n in plan.notes)


def test_strategy_ignores_thesis_managed_positions(engine, settings, prices):
    from tradebot.models import ThesisRequest
    settings.strategy.universe = {"in": ["NSE:AAA", "NSE:BBB"]}
    settings.strategy.max_positions = 2
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    fake = engine.data.fake
    make_series(fake, "NSE:AAA", [100 + i for i in range(60)])
    make_series(fake, "NSE:BBB", [200 + i * 0.5 for i in range(60)])
    t = engine.open_thesis(ThesisRequest(symbol="NSE:AAA", text="event", size_notional=2_000, stop_pct=5), execute=True)
    assert t.status.value == "open"
    strat = TrendStrategy(engine)
    plan = strat.plan(Market.IN, "paper")
    # AAA is thesis-managed: not counted, not sold, not bought again; BBB fills the free slot
    assert [o.symbol for o in plan.orders] == ["NSE:BBB"]
    assert any("thesis-managed" in n for n in plan.notes)
    fake.prices["NSE:AAA"] = 120.0  # far below SMA20: a strategy position would be sold, a thesis one is left alone
    plan2 = strat.plan(Market.IN, "paper")
    assert all(o.symbol != "NSE:AAA" for o in plan2.orders)
