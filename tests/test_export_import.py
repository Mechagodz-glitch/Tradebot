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
    assert eng2.orders() == []  # venue history is not re-created
    # idempotent
    res2 = eng2.import_state(data)
    assert res2 == {"theses_added": 0, "journal_added": 0, "exported_at": data["exported_at"]}
