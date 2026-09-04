import pytest

from tradebot.errors import SymbolError
from tradebot.models import Market
from tradebot.symbols import parse_symbol


def test_crypto_pair_forms():
    for raw in ("btc-usd", "BTC/USD", "BTC-USD"):
        i = parse_symbol(raw)
        assert (i.symbol, i.market, i.base, i.currency) == ("BTC-USD", Market.CRYPTO, "BTC", "USD")


def test_crypto_default_quote_when_market_forced():
    i = parse_symbol("eth", Market.CRYPTO)
    assert i.symbol == "ETH-USD"


def test_indian_symbol():
    i = parse_symbol("nse:reliance")
    assert (i.symbol, i.market, i.currency, i.exchange) == ("NSE:RELIANCE", Market.IN, "INR", "NSE")
    assert parse_symbol("INFY", Market.IN).symbol == "NSE:INFY"


def test_us_symbol():
    i = parse_symbol("aapl")
    assert (i.symbol, i.market, i.currency) == ("AAPL", Market.US, "USD")
    assert parse_symbol("BRK.B").symbol == "BRK.B"


def test_errors():
    with pytest.raises(SymbolError):
        parse_symbol("")
    with pytest.raises(SymbolError):
        parse_symbol("XYZ:FOO")
    with pytest.raises(SymbolError):
        parse_symbol("BTC-USD", Market.US)
