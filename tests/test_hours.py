from datetime import datetime, timezone

import pytest

from tradebot.errors import RiskRejected
from tradebot.hours import is_open, market_session
from tradebot.models import Market, OrderRequest, Side


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_nse_session():
    assert is_open(Market.IN, utc(2026, 9, 7, 4, 0)) is True       # Mon 09:30 IST
    assert is_open(Market.IN, utc(2026, 9, 7, 3, 44)) is False     # Mon 09:14 IST
    assert is_open(Market.IN, utc(2026, 9, 7, 10, 0)) is False     # Mon 15:30 IST (close)
    assert is_open(Market.IN, utc(2026, 9, 5, 5, 0)) is False      # Saturday
    s = market_session(Market.IN, utc(2026, 9, 4, 21, 0))          # Fri evening -> next Monday
    assert s["open"] is False and s["next_open"].startswith("2026-09-07T09:15")


def test_us_session_and_crypto():
    assert is_open(Market.US, utc(2026, 9, 4, 14, 0)) is True      # Fri 10:00 ET
    assert is_open(Market.US, utc(2026, 9, 4, 21, 0)) is False     # Fri 17:00 ET
    assert is_open(Market.CRYPTO, utc(2026, 9, 5, 3, 0)) is True


def test_live_orders_blocked_outside_hours(engine, settings, monkeypatch):
    settings.live_trading_enabled = True
    settings.kite_api_key, settings.kite_access_token = "k", "t"
    monkeypatch.setattr("tradebot.risk.market_session", lambda m: {"open": False, "detail": "closed (test)"})
    with pytest.raises(RiskRejected) as e:
        engine.place_order(OrderRequest(symbol="NSE:RELIANCE", side=Side.BUY, qty=1, venue="kite"))
    assert e.value.code == "market_closed"
    # paper is never blocked by hours
    assert engine.place_order(OrderRequest(symbol="NSE:RELIANCE", side=Side.BUY, qty=1)).status.value == "filled"


def test_per_market_allowed_symbols(engine, settings):
    settings.risk.allowed_symbols = {"in": ["NSE:INFY"]}
    with pytest.raises(RiskRejected) as e:
        engine.place_order(OrderRequest(symbol="NSE:RELIANCE", side=Side.BUY, qty=1))
    assert e.value.code == "symbol_not_allowed"
    assert engine.place_order(OrderRequest(symbol="AAPL", side=Side.BUY, qty=1)).status.value == "filled"  # us unrestricted
