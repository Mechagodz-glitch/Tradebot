from datetime import datetime

from tradebot.config import Settings
from tradebot.data.kite_data import IST, KiteData
from tradebot.data.registry import MarketData
from tradebot.models import Market
from tradebot.symbols import parse_symbol


def test_kite_not_available_without_token():
    s = Settings(kite_api_key="k")
    assert KiteData(s).available() is False
    s.kite_access_token = "t"
    assert KiteData(s).available() is True


def test_registry_skips_kite_until_configured():
    s = Settings()
    names = [p.name for p in MarketData(s).providers_for(Market.IN)]
    assert "kite" not in names and names[0] == "groww"
    s.kite_api_key, s.kite_access_token = "k", "t"
    names = [p.name for p in MarketData(s).providers_for(Market.IN)]
    assert names[0] == "kite"


def test_key_and_timestamps():
    assert KiteData._key(parse_symbol("nse:reliance")) == "NSE:RELIANCE"
    assert KiteData._key(parse_symbol("bse:reliance")) == "BSE:RELIANCE"
    naive = datetime(2026, 9, 4, 15, 29, 0)
    assert KiteData._ts(naive).tzinfo == IST
    assert KiteData._ts("2026-09-04 15:29:00").tzinfo == IST
    assert KiteData._ts(None).tzinfo is not None
