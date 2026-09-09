import pytest
from tradebot.config import Settings
from tradebot.engine import TradingEngine
from tradebot.models import Market, OrderRequest, Side, ThesisRequest
from tradebot.store import Store


def test_export_then_import_into_fresh_db(engine, settings, prices, tmp_path):
    from tests.conftest import FakeMarketData
    engine.note("thesis rationale", symbol="AAPL", kind="thesis")
    engine.open_thesis(ThesisRequest(symbol="NSE:RELIANCE", text="planned", size_notional=2_000), execute=False)
    engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1))
    data = engine.export_state()
    assert len(data["theses"]) == 1 and len(data["orders"]) == 1 and len(data["journal"]) >= 3

    s2 = Settings(db_path=str(tmp_path / "fresh.db")); s2.root = str(tmp_path)
    eng2 = TradingEngine(s2, Store(s2.resolve(s2.db_path)), FakeMarketData(s2))
    res = eng2.import_state(data)
    assert res["theses_added"] == 1 and res["journal_added"] == len(data["journal"])
    assert eng2.theses()[0].symbol == "NSE:RELIANCE"
    assert len(eng2.orders()) == 1 and res["orders_added"] == 1  # order history travels with the snapshot
    # idempotent
    res2 = eng2.import_state(data)
    assert res2["theses_added"] == 0 and res2["journal_added"] == 0 and res2["orders_added"] == 0 and res2["exported_at"] == data["exported_at"]


def test_import_updates_newer_theses_and_adds_orders(engine, settings, prices, tmp_path):
    from datetime import timedelta
    from tests.conftest import FakeMarketData
    from tradebot.models import ThesisStatus
    t = engine.open_thesis(ThesisRequest(symbol="NSE:RELIANCE", text="planned", size_notional=2_000), execute=False)
    snap1 = engine.export_state()
    s2 = Settings(db_path=str(tmp_path / "other.db")); s2.root = str(tmp_path)
    eng2 = TradingEngine(s2, Store(s2.resolve(s2.db_path)), FakeMarketData(s2))
    eng2.import_state(snap1)
    # the other machine enters the thesis and exports; our import must pick up the OPEN state and the order
    eng2.settings.paper.starting_cash["in"] = 10_000
    eng2.settings.risk.max_order_notional["INR"] = 4_000
    eng2.settings.risk.max_position_notional["INR"] = 4_500
    eng2.data.fake.prices["NSE:RELIANCE"] = 1_300.0
    eng2.enter_thesis(eng2.store.get_thesis(t.id))
    snap2 = eng2.export_state()
    res = engine.import_state(snap2)
    assert res["theses_updated"] == 1 and res["orders_added"] == 1 and res["fills_added"] == 1
    assert engine.store.get_thesis(t.id).status == ThesisStatus.OPEN


def test_attach_from_venue_position(engine, settings, prices):
    settings.paper.starting_cash["in"] = 10_000
    settings.risk.max_order_notional["INR"] = 4_000
    settings.risk.max_position_notional["INR"] = 4_500
    prices["NSE:OIL"] = 486.0
    engine.place_order(OrderRequest(symbol="NSE:OIL", side=Side.BUY, qty=4, reason="placed elsewhere"))
    t = engine.open_thesis(ThesisRequest(symbol="NSE:OIL", text="crude", size_notional=2_400), execute=False)
    t = engine.attach_thesis(t.id)  # no qty / price: read from the paper position
    assert t.status.value == "open" and t.qty == 4 and t.entry_price == pytest.approx(486.0 * 1.0005)


def test_import_preserves_timestamps_so_replay_order_does_not_matter(engine, settings, prices, tmp_path):
    from tests.conftest import FakeMarketData
    from tradebot.models import ThesisStatus
    t = engine.open_thesis(ThesisRequest(symbol="NSE:RELIANCE", text="planned", size_notional=2_000), execute=False)
    older = engine.export_state()
    engine.settings.paper.starting_cash["in"] = 10_000
    engine.settings.risk.max_order_notional["INR"] = 4_000
    engine.settings.risk.max_position_notional["INR"] = 4_500
    engine.enter_thesis(engine.store.get_thesis(t.id))
    newer = engine.export_state()
    newer_ts = engine.store.get_thesis(t.id).updated_at

    s2 = Settings(db_path=str(tmp_path / "replay.db")); s2.root = str(tmp_path)
    eng2 = TradingEngine(s2, Store(s2.resolve(s2.db_path)), FakeMarketData(s2))
    eng2.import_state(newer)                                   # newest first
    assert eng2.store.get_thesis(t.id).updated_at == newer_ts  # snapshot timestamp kept, not stamped "now"
    res = eng2.import_state(older)                             # an older snapshot must not regress the record
    assert res["theses_updated"] == 0
    assert eng2.store.get_thesis(t.id).status == ThesisStatus.OPEN
    res = eng2.import_state(older, force=True)                 # unless explicitly forced
    assert res["theses_updated"] == 1
    assert eng2.store.get_thesis(t.id).status == ThesisStatus.PLANNED


def test_import_restores_equity_history_once(engine, settings, tmp_path):
    from datetime import datetime, timezone
    from tests.conftest import FakeMarketData
    from tradebot.models import EquityPoint
    for i in range(3):
        engine.store.add_equity_point(EquityPoint(venue="paper", market=Market.IN, ts=datetime(2026, 9, 1 + i, 10, tzinfo=timezone.utc),
                                                  cash=10_000 - i, positions_value=i, equity=10_000))
    snap = engine.export_state()
    assert len(snap["equity"]["paper/in"]) == 3
    s2 = Settings(db_path=str(tmp_path / "eq.db")); s2.root = str(tmp_path)
    eng2 = TradingEngine(s2, Store(s2.resolve(s2.db_path)), FakeMarketData(s2))
    assert eng2.import_state(snap)["equity_added"] == 3
    assert eng2.import_state(snap)["equity_added"] == 0
    assert len(eng2.store.equity_curve("paper", Market.IN)) == 3
