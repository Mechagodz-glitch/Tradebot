from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Market(str, Enum):
    US = "us"
    IN = "in"
    CRYPTO = "crypto"


MARKET_CURRENCY = {Market.US: "USD", Market.IN: "INR", Market.CRYPTO: "USD"}


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)


class Instrument(BaseModel):
    """A tradeable instrument in canonical form.

    ``symbol`` is the canonical symbol used everywhere in Tradebot:
      us:     ``AAPL``
      in:     ``NSE:RELIANCE``
      crypto: ``BTC-USD``
    """

    symbol: str
    market: Market
    base: str
    currency: str
    exchange: Optional[str] = None


class Quote(BaseModel):
    symbol: str
    market: Market
    currency: str
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[float] = None
    ts: datetime = Field(default_factory=utcnow)
    source: str = ""

    @property
    def mid(self) -> float:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last


class Candle(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class OrderRequest(BaseModel):
    symbol: str
    side: Side
    qty: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = Field(default=None, gt=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    tif: TimeInForce = TimeInForce.DAY
    venue: str = "paper"
    market: Optional[Market] = None
    reason: Optional[str] = None
    client_order_id: Optional[str] = None
    strategy: Optional[str] = None

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def validate_prices(self) -> None:
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError("limit_price is required for limit and stop_limit orders")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError("stop_price is required for stop and stop_limit orders")


class Order(BaseModel):
    id: str
    venue: str
    venue_order_id: Optional[str] = None
    symbol: str
    market: Market
    currency: str
    side: Side
    qty: float
    filled_qty: float = 0.0
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.NEW
    avg_fill_price: Optional[float] = None
    fees: float = 0.0
    reason: Optional[str] = None
    strategy: Optional[str] = None
    reject_reason: Optional[str] = None
    client_order_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def remaining_qty(self) -> float:
        return max(self.qty - self.filled_qty, 0.0)


class Fill(BaseModel):
    id: str
    order_id: str
    venue: str
    symbol: str
    market: Market
    side: Side
    qty: float
    price: float
    fee: float = 0.0
    ts: datetime = Field(default_factory=utcnow)


class Position(BaseModel):
    venue: str
    symbol: str
    market: Market
    currency: str
    qty: float
    avg_price: float
    market_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: float = 0.0
    ts: datetime = Field(default_factory=utcnow)


class Account(BaseModel):
    venue: str
    market: Market
    currency: str
    cash: float
    positions_value: float = 0.0
    equity: float = 0.0
    buying_power: Optional[float] = None
    starting_cash: Optional[float] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    ts: datetime = Field(default_factory=utcnow)


class EquityPoint(BaseModel):
    venue: str
    market: Market
    ts: datetime
    cash: float
    positions_value: float
    equity: float


class JournalEntry(BaseModel):
    id: int | None = None
    ts: datetime = Field(default_factory=utcnow)
    kind: str  # order, fill, note, risk, system
    venue: Optional[str] = None
    symbol: Optional[str] = None
    order_id: Optional[str] = None
    text: str
    data: Optional[dict] = None


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    latency_ms: Optional[int] = None
