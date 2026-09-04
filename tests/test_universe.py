import json

import pytest

from tradebot.errors import DataError, RiskRejected
from tradebot.models import OrderRequest, Side
from tradebot.universe import load_symbol_list


def test_load_symbol_list_forms(tmp_path):
    assert load_symbol_list(["nse:a", "NSE:B"]) == ["NSE:A", "NSE:B"]
    assert load_symbol_list(None) == []
    j = tmp_path / "u.json"; j.write_text(json.dumps({"symbols": ["nse:x", "nse:y"]}))
    assert load_symbol_list(f"file:{j}") == ["NSE:X", "NSE:Y"]
    t = tmp_path / "u.txt"; t.write_text("# comment\nnse:p\n\nNSE:Q\n")
    assert load_symbol_list(f"file:{t}") == ["NSE:P", "NSE:Q"]
    with pytest.raises(DataError):
        load_symbol_list("file:/nonexistent.json")


def test_risk_uses_file_whitelist(engine, settings, tmp_path):
    f = tmp_path / "in.json"; f.write_text(json.dumps({"symbols": ["NSE:INFY"]}))
    settings.risk.allowed_symbols = {"in": f"file:{f}"}
    with pytest.raises(RiskRejected):
        engine.place_order(OrderRequest(symbol="NSE:RELIANCE", side=Side.BUY, qty=1))
    engine.data.fake.prices["NSE:INFY"] = 1_130.0
    settings.paper.starting_cash["in"] = 1_000_000
    settings.risk.max_order_notional["INR"] = 10_000
    assert engine.place_order(OrderRequest(symbol="NSE:INFY", side=Side.BUY, qty=1)).status.value == "filled"
