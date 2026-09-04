"""Canonical symbol parsing.

Conventions (case insensitive on input, upper case canonical):
  * ``NSE:RELIANCE`` / ``BSE:RELIANCE``  -> Indian equity (market ``in``, currency INR)
  * ``BTC-USD`` / ``BTC/USD`` / ``BTC-INR``  -> crypto spot pair (market ``crypto``)
  * ``AAPL``                              -> US equity (market ``us``, currency USD)
An explicit ``market`` argument overrides detection.
"""

from __future__ import annotations

from typing import Optional

from .errors import SymbolError
from .models import Instrument, Market

INDIAN_EXCHANGES = {"NSE", "BSE"}
FIAT = {"USD", "USDT", "USDC", "INR", "EUR", "GBP"}


def parse_symbol(raw: str, market: Optional[Market] = None) -> Instrument:
    s = (raw or "").strip().upper().replace("/", "-")
    if not s:
        raise SymbolError("empty symbol")

    if ":" in s:
        exch, sym = s.split(":", 1)
        if exch in INDIAN_EXCHANGES:
            if market not in (None, Market.IN):
                raise SymbolError(f"{raw} is an Indian exchange symbol but market={market.value}")
            return Instrument(symbol=f"{exch}:{sym}", market=Market.IN, base=sym, currency="INR", exchange=exch)
        raise SymbolError(f"unknown exchange prefix {exch!r} in {raw!r}; use NSE: or BSE:")

    if "-" in s or market == Market.CRYPTO:
        if "-" not in s:
            s = f"{s}-USD"
        base, quote = s.split("-", 1)
        if market not in (None, Market.CRYPTO):
            raise SymbolError(f"{raw} looks like a crypto pair but market={market.value}")
        return Instrument(symbol=f"{base}-{quote}", market=Market.CRYPTO, base=base, currency=quote, exchange=None)

    if market == Market.IN:
        return Instrument(symbol=f"NSE:{s}", market=Market.IN, base=s, currency="INR", exchange="NSE")

    if not s.replace(".", "").isalnum():
        raise SymbolError(f"invalid US symbol {raw!r}")
    return Instrument(symbol=s, market=Market.US, base=s, currency="USD", exchange=None)
