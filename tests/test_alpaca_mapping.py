from enum import Enum

from tradebot.brokers.alpaca import AlpacaBroker
from tradebot.models import Market


class _AssetClass(str, Enum):
    CRYPTO = "crypto"
    US_EQUITY = "us_equity"


def test_order_symbol_form_maps_to_crypto():
    assert AlpacaBroker._canon("BTC/USD") == ("BTC-USD", Market.CRYPTO)


def test_position_symbol_form_uses_asset_class():
    # Positions come back without a separator; asset_class disambiguates.
    assert AlpacaBroker._canon("BTCUSD", _AssetClass.CRYPTO) == ("BTC-USD", Market.CRYPTO)
    assert AlpacaBroker._canon("ETHUSDT", _AssetClass.CRYPTO) == ("ETH-USDT", Market.CRYPTO)
    assert AlpacaBroker._canon("ETHUSDC", "crypto") == ("ETH-USDC", Market.CRYPTO)
    assert AlpacaBroker._canon("ETHBTC", _AssetClass.CRYPTO) == ("ETH-BTC", Market.CRYPTO)


def test_equity_symbols_stay_us():
    assert AlpacaBroker._canon("AAPL", _AssetClass.US_EQUITY) == ("AAPL", Market.US)
    assert AlpacaBroker._canon("USD", None) == ("USD", Market.US)  # no false crypto match without asset_class
