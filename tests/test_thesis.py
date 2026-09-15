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


def test_attach_manual_fill_then_check(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    prices["NSE:OIL"] = 486.0
    t = engine.open_thesis(ThesisRequest(symbol="NSE:OIL", text="crude", size_notional=2_400, stop_pct=5, target_pct=8), execute=False)
    t = engine.attach_thesis(t.id, qty=4, entry_price=486.58, venue_order_id="260907000123")
    assert t.status == ThesisStatus.OPEN and t.qty == 4 and t.entry_order_id == "260907000123"
    with pytest.raises(BrokerError):
        engine.attach_thesis(t.id, qty=1, entry_price=1)  # already open
    rows = engine.check_theses()
    assert rows[0]["stop"] == pytest.approx(486.58 * 0.95) and rows[0]["action"] is None


def test_check_reports_failed_exit_instead_of_raising(engine, settings, prices, monkeypatch):
    settings.paper.starting_cash["in"] = 10_000
    prices["NSE:OIL"] = 486.0
    t = engine.open_thesis(ThesisRequest(symbol="NSE:OIL", text="crude", size_notional=2_400, stop_pct=5), execute=True)
    prices["NSE:OIL"] = 400.0
    def boom(*a, **k):
        raise BrokerError("venue rejected: No IPs configured", code="broker_error")
    monkeypatch.setattr(engine, "place_order", boom)
    rows = engine.check_theses(execute=True)
    assert rows[0]["action"] == "exit failed" and "No IPs" in rows[0]["detail"]
    assert engine.theses()[0].status == ThesisStatus.OPEN
    assert any("exit FAILED" in j.text for j in engine.journal(limit=5))


def test_armed_thesis_enters_only_inside_the_band(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    prices["NSE:CHENNPETRO"] = 1_580.0
    t = engine.open_thesis(ThesisRequest(symbol="NSE:CHENNPETRO", text="refiner pullback", size_notional=3_000, stop_pct=5, target_pct=8,
                                         entry_min=1_500, entry_max=1_540), execute=False)
    assert t.status == ThesisStatus.PLANNED and t.auto_enter is True
    rows = engine.check_theses(execute=True)
    assert rows[0]["action"] is None and "outside" in rows[0]["detail"]
    assert engine.store.get_thesis(t.id).status == ThesisStatus.PLANNED
    prices["NSE:CHENNPETRO"] = 1_520.0
    rows = engine.check_theses(execute=False)
    assert rows[0]["action"] == "would enter"
    rows = engine.check_theses(execute=True)
    assert rows[0]["action"] == "enter" and rows[0]["status"] == "open"
    opened = engine.store.get_thesis(t.id)
    assert opened.status == ThesisStatus.OPEN and opened.qty == 1 and opened.entry_price == pytest.approx(1_520.0 * 1.0005, rel=1e-3)
    # a second pass does not re-enter, and the open thesis is now monitored for stop/target
    rows = engine.check_theses(execute=True)
    assert [r["id"] for r in rows] == [t.id] and rows[0]["status"] == "open"


def test_arm_and_disarm_planned_thesis_and_expiry(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    prices["NSE:GLAND"] = 2_900.0
    t = engine.open_thesis(ThesisRequest(symbol="NSE:GLAND", text="pharma exporter", size_notional=3_000), execute=False)
    assert engine.check_theses() == []                                    # not armed: ignored by the executor
    engine.arm_thesis(t.id, entry_max=2_950)
    assert engine.check_theses(execute=False)[0]["action"] == "would enter"
    engine.arm_thesis(t.id, auto_enter=False)
    assert engine.check_theses() == []
    t2 = engine.open_thesis(ThesisRequest(symbol="NSE:GLAND", text="stale", size_notional=3_000, expires_at=utcnow() - timedelta(days=1),
                                          auto_enter=True), execute=False)
    rows = engine.check_theses(execute=True)
    assert rows[0]["id"] == t2.id and rows[0]["status"] == "canceled"


def test_store_migration_adds_new_thesis_columns(tmp_path):
    from sqlalchemy import text
    from tradebot.store import Store
    s1 = Store(str(tmp_path / "old.db"))
    with s1.engine.begin() as conn:
        conn.execute(text("ALTER TABLE theses DROP COLUMN auto_enter"))
        conn.execute(text("ALTER TABLE theses DROP COLUMN entry_min"))
    s2 = Store(str(tmp_path / "old.db"))                                  # re-open: missing columns are added back
    with s2.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(theses)"))}
    assert {"auto_enter", "entry_min", "entry_max"} <= cols
