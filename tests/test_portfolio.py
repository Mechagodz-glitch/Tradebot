from datetime import date

import pytest

from tradebot.models import Market, OrderRequest, Side
from tradebot.portfolio import PortfolioModel, Sleeve, annual_dps, calendar, load_dividends, load_model, plan, record_months, status

MODEL = PortfolioModel(market=Market.IN, venue="paper", currency="INR", cash_buffer=100, sleeves={
    "core": Sleeve(weight=0.5, holdings={"NSE:NIFTYBEES": 1, "NSE:GOLDBEES": 1}),
    "income": Sleeve(weight=0.5, holdings={"NSE:COALINDIA": 1, "NSE:HCLTECH": 1, "NSE:IOC": 0}),
})
DIVS = {
    "NSE:COALINDIA": {"dps_fy26": 26.5, "payments": [{"month": 11, "amount": 10.25}, {"month": 2, "amount": 5.5}, {"month": 3, "amount": 5.5}, {"month": 8, "amount": 5.25}]},
    "NSE:HCLTECH": {"dps_fy26": 60, "payments": [{"month": 1, "amount": 12}, {"month": 5, "amount": 24}, {"month": 7, "amount": 12}, {"month": 10, "amount": 12}]},
}


def _prep(engine, prices):
    engine.settings.paper.starting_cash["in"] = 20_000
    engine.settings.risk.max_order_notional["INR"] = 15_000
    engine.settings.risk.max_position_notional["INR"] = 16_000
    prices.update({"NSE:NIFTYBEES": 268.0, "NSE:GOLDBEES": 125.0, "NSE:COALINDIA": 430.0, "NSE:HCLTECH": 1220.0, "NSE:IOC": 135.0})


def test_targets_normalise_and_ignore_zero_weights():
    t = MODEL.targets()
    assert set(t) == {"NSE:NIFTYBEES", "NSE:GOLDBEES", "NSE:COALINDIA", "NSE:HCLTECH"}   # IOC weight 0 is out
    assert all(abs(v - 0.25) < 1e-9 for v in t.values()) and abs(sum(t.values()) - 1) < 1e-9
    assert MODEL.sleeve_of("NSE:GOLDBEES") == "core" and MODEL.sleeve_of("NSE:X") is None


def test_repo_model_and_calendar_files_load(engine):
    m = load_model(".")
    t = m.targets()
    assert abs(sum(t.values()) - 1) < 1e-6 and "NSE:IOC" not in t and "NSE:COALINDIA" in t
    d = load_dividends(".")
    assert annual_dps(d["NSE:COALINDIA"]) == 26.5
    assert record_months(d["NSE:HCLTECH"]) == {12, 4, 6, 9}      # payout months 1,5,7,10 minus one


def test_plan_fills_largest_gap_first_whole_shares_and_prefers_record_dates(engine, prices):
    _prep(engine, prices)
    # already hold some gold: the plan should fill the other three first
    engine.place_order(OrderRequest(symbol="NSE:GOLDBEES", side=Side.BUY, qty=8, venue="paper"))
    res = plan(engine, MODEL, contribution=10_000, dividends=DIVS, today=date(2026, 9, 12))
    bought = {b["symbol"]: b for b in res["buys"]}
    assert bought["NSE:GOLDBEES"]["gap_before"] < bought["NSE:COALINDIA"]["gap_before"]   # gold already partly held
    assert res["buys"][0]["symbol"] != "NSE:GOLDBEES"                                    # largest gaps come first
    assert bought["NSE:COALINDIA"]["record_soon"] is True         # Oct record date (Nov payout) within this/next month
    assert bought["NSE:HCLTECH"]["record_soon"] is True          # Sep record date (Oct payout)
    assert all(isinstance(b["qty"], int) and b["qty"] >= 1 for b in res["buys"])
    # a high-priced name whose gap is smaller than one share still gets one share once the gap is at least half a share
    small = PortfolioModel(market=Market.IN, venue="paper", currency="INR", sleeves={"x": Sleeve(weight=1, holdings={"NSE:HCLTECH": 1, "NSE:GOLDBEES": 9})})
    r2 = plan(engine, small, contribution=800, dividends=DIVS, today=date(2026, 9, 12))
    hcl = next((b for b in r2["buys"] if b["symbol"] == "NSE:HCLTECH"), None)
    assert hcl is not None and hcl["qty"] == 1                     # gap ~ 10% of ~29k budget = ~2.9k >= half of 1,220
    assert res["spend"] <= res["budget"] and res["unspent"] >= 0
    for b in res["buys"]:
        assert b["limit"] * 20 == round(b["limit"] * 20)          # 0.05 tick
        assert b["notional"] <= engine.settings.risk.max_order_notional["INR"] and not b["risk_flags"]
    # the budget includes idle cash above the buffer: paper cash 20,000 - 1,000 gold = 19,000, minus buffer 100
    assert res["budget"] == pytest.approx(10_000 + 19_000 - 100, abs=5)


def test_plan_skips_thesis_managed_symbols_and_flags_risk(engine, prices):
    from tradebot.models import ThesisRequest
    _prep(engine, prices)
    engine.settings.risk.max_order_notional["INR"] = 2_000
    t = engine.open_thesis(ThesisRequest(symbol="NSE:HCLTECH", text="trade", size_notional=2_000, venue="paper"), execute=False)
    engine.enter_thesis(engine.store.get_thesis(t.id))
    res = plan(engine, MODEL, contribution=12_000, dividends=DIVS, today=date(2026, 9, 12))
    assert any(s["symbol"] == "NSE:HCLTECH" for s in res["skipped"])
    assert all(b["symbol"] != "NSE:HCLTECH" for b in res["buys"])
    assert any(b["risk_flags"] for b in res["buys"])              # 12k over three names exceeds a 2k per-order cap


def test_status_and_calendar(engine, prices):
    _prep(engine, prices)
    engine.place_order(OrderRequest(symbol="NSE:COALINDIA", side=Side.BUY, qty=10, venue="paper"))
    engine.place_order(OrderRequest(symbol="NSE:HCLTECH", side=Side.BUY, qty=2, venue="paper"))
    st = status(engine, MODEL, dividends=DIVS)
    rows = {r["symbol"]: r for r in st["rows"]}
    assert rows["NSE:COALINDIA"]["qty"] == 10 and rows["NSE:COALINDIA"]["annual_dividend"] == pytest.approx(265.0)
    assert st["annual_dividend"] == pytest.approx(265.0 + 120.0)
    assert st["dividends_by_month"]["11"] == pytest.approx(102.5) and st["dividends_by_month"]["1"] == pytest.approx(24.0)
    assert abs(sum(r["current_w"] for r in st["rows"]) - 1) < 1e-9
    cal = calendar(engine, MODEL, dividends=DIVS, months=2, today=date(2026, 9, 12))
    syms = [(e["symbol"], e["pay_month"]) for e in cal["events"]]
    assert ("NSE:HCLTECH", 10) in syms and ("NSE:COALINDIA", 11) in syms and ("NSE:HCLTECH", 1) not in syms
    assert cal["expected_total"] == pytest.approx(2 * 12 + 10 * 10.25)
    assert cal["by_month"]["10"] == pytest.approx(24.0)
