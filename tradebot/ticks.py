"""Price rounding to exchange tick sizes. NSE cash quotes in multiples of 0.05 (0.01 below 250, and
0.05 is a valid multiple of 0.01 too), US equities in cents, crypto to the cent."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Optional

from .models import Market, Side

TICKS = {Market.IN: Decimal("0.05"), Market.US: Decimal("0.01"), Market.CRYPTO: Decimal("0.01")}


def tick_for(market: Market) -> float:
    return float(TICKS[market])


def round_to_tick(price: float, market: Market, side: Optional[Side] = None) -> float:
    """Round ``price`` to the market's tick. Buys round up and sells round down (keeps a marketable
    limit marketable); with no side, round to nearest."""
    tick = TICKS[market]
    p = Decimal(str(price))
    q = (p / tick)
    if side == Side.BUY:
        q = q.to_integral_value(rounding=ROUND_CEILING)
    elif side == Side.SELL:
        q = q.to_integral_value(rounding=ROUND_FLOOR)
    else:
        q = q.to_integral_value(rounding=ROUND_HALF_UP)
    return float(q * tick)
