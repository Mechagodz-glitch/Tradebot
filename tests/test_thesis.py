from datetime import timedelta

import pytest

from tradebot.errors import BrokerError
from tradebot.models import Market, ThesisRequest, ThesisStatus, utcnow


def test_thesis_lifecycle_stop(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    prices["NSE:SWIGGY"] = 276.0
    t = engine.open_thesis(ThesisRequest(symbol="nse:swiggy", text="post MSCI deletion rebound", size_notional=3_000, stop_pct=5,
                                         target_pct=10, confidence=0.6), execute=True)
    assert t.status == ThesisStatus.OPEN and t.qty == 10 and t.entry_price == pytest.approx(276.0 * 1.0005)
    rows = engine.check_theses()
    assert rows[0]["action"] is None
    prices["NSE:SWIGGY"] = 260.0  # -5.8%
    rows = engine.check_theses(execute=False)
    assert rows[0]["action"] == "would close" and "stop" in rows[0]["detail"]
    assert engine.theses()[0].status == ThesisStatus.OPEN  # dry run changed nothing
    rows = engine.check_theses(execute=True)
    assert rows[0]["status"] == "closed"
    closed = engine.theses(all_=True)[0]
    assert closed.status == ThesisStatus.CLOSED and closed.realized_pnl < 0 and closed.exit_order_id
    assert engine.positions(market=Market.IN) == []
    assert any(j.kind == "thesis" for j in engine.journal(limit=20))


def test_thesis_target_and_expiry(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    prices["NSE:GICRE"] = 358.0
    prices["NSE:LICI"] = 415.0
    a = engine.open_thesis(ThesisRequest(symbol="NSE:GICRE", text="NSE IPO selling shareholder", size_notional=2_500, stop_pct=5, target_pct=8), execute=True)
    b = engine.open_thesis(ThesisRequest(symbol="NSE:LICI", text="largest NSE holder", size_notional=2_000, stop_pct=4,
                                         expires_at=utcnow() - timedelta(minutes=1)), execute=True)
    prices["NSE:GICRE"] = 358.0 * 1.09
    rows = {r["id"]: r for r in engine.check_theses(execute=True)}
    assert "target" in rows[a.id]["detail"] and rows[a.id]["status"] == "closed"
    assert "expired" in rows[b.id]["detail"] and rows[b.id]["status"] == "closed"
    assert engine.theses(all_=True)[0].realized_pnl is not None


def test_planned_thesis_then_enter_and_manual_close(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    prices["NSE:TEJASNET"] = 614.0
    t = engine.open_thesis(ThesisRequest(symbol="NSE:TEJASNET", text="momentum", size_notional=3_000), execute=False)
    assert t.status == ThesisStatus.PLANNED and engine.orders() == []
    t = engine.enter_thesis(t)
    assert t.status == ThesisStatus.OPEN and t.qty == 4
    with pytest.raises(BrokerError):
        engine.enter_thesis(t)
    c = engine.close_thesis(t.id, reason="taking profit early")
    assert c.status == ThesisStatus.CLOSED and engine.positions(market=Market.IN) == []


def test_short_not_supported(engine):
    with pytest.raises(BrokerError):
        engine.open_thesis(ThesisRequest(symbol="AAPL", text="x", size_notional=100, direction="short"))
