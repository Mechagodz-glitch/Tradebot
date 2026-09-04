"""Alpaca (US equities + crypto). Paper endpoint by default (alpaca.paper: true)."""

from __future__ import annotations

from typing import Optional

from ..errors import BrokerError
from ..models import (
    Account, Instrument, JournalEntry, Market, Order, OrderRequest, OrderStatus, OrderType, Position, Side, TimeInForce, utcnow,
)
from .base import Broker

STATUS_MAP = {
    "new": OrderStatus.ACCEPTED, "accepted": OrderStatus.ACCEPTED, "pending_new": OrderStatus.NEW,
    "accepted_for_bidding": OrderStatus.ACCEPTED, "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED, "canceled": OrderStatus.CANCELED, "pending_cancel": OrderStatus.ACCEPTED,
    "expired": OrderStatus.EXPIRED, "rejected": OrderStatus.REJECTED, "done_for_day": OrderStatus.EXPIRED,
    "replaced": OrderStatus.CANCELED, "stopped": OrderStatus.FILLED, "suspended": OrderStatus.ACCEPTED,
    "calculated": OrderStatus.ACCEPTED, "held": OrderStatus.ACCEPTED, "pending_replace": OrderStatus.ACCEPTED,
}


class AlpacaBroker(Broker):
    name = "alpaca"
    markets = (Market.US, Market.CRYPTO)

    def __init__(self, settings, store, data):
        super().__init__(settings, store, data)
        self.live = not settings.alpaca.paper
        self._client = None

    def available(self) -> bool:
        return bool(self.settings.alpaca_api_key and self.settings.alpaca_secret_key)

    @property
    def client(self):
        if self._client is None:
            from alpaca.trading.client import TradingClient
            if not self.available():
                raise BrokerError("alpaca: ALPACA_API_KEY / ALPACA_SECRET_KEY not set", code="credentials_missing")
            self._client = TradingClient(self.settings.alpaca_api_key, self.settings.alpaca_secret_key, paper=self.settings.alpaca.paper)
        return self._client

    @staticmethod
    def _sym(inst: Instrument) -> str:
        return f"{inst.base}/{inst.currency}" if inst.market == Market.CRYPTO else inst.symbol

    @staticmethod
    def _canon(sym: str, asset_class=None) -> tuple[str, Market]:
        """Map Alpaca symbols back to canonical form. Orders use ``BTC/USD`` but positions report
        ``BTCUSD`` with asset_class crypto, so split on a known quote currency."""
        if "/" in sym:
            return sym.replace("/", "-"), Market.CRYPTO
        is_crypto = asset_class is not None and str(getattr(asset_class, "value", asset_class)).lower() == "crypto"
        if is_crypto:
            for quote in ("USDT", "USDC", "USD", "BTC"):
                if sym.endswith(quote) and len(sym) > len(quote):
                    return f"{sym[:-len(quote)]}-{quote}", Market.CRYPTO
            return sym, Market.CRYPTO
        return sym, Market.US

    def _check(self) -> str:
        a = self.client.get_account()
        return f"{'PAPER' if self.settings.alpaca.paper else 'LIVE'} account {a.account_number} status={a.status} equity={a.equity}"

    def account(self, market: Market) -> Account:
        a = self.client.get_account()
        positions = self.positions(market, mark=False)
        pv = sum(p.market_value or 0.0 for p in positions)
        return Account(venue=self.name, market=market, currency="USD", cash=float(a.cash), positions_value=pv,
                       equity=float(a.equity), buying_power=float(a.buying_power),
                       unrealized_pnl=sum(p.unrealized_pnl or 0.0 for p in positions))

    def positions(self, market: Optional[Market] = None, mark: bool = True) -> list[Position]:
        out = []
        for p in self.client.get_all_positions():
            sym, m = self._canon(p.symbol, getattr(p, "asset_class", None))
            if market and m != market:
                continue
            out.append(Position(venue=self.name, symbol=sym, market=m, currency="USD", qty=float(p.qty),
                                avg_price=float(p.avg_entry_price), market_price=float(p.current_price or 0) or None,
                                market_value=float(p.market_value or 0), unrealized_pnl=float(p.unrealized_pl or 0)))
        return out

    def place_order(self, req: OrderRequest, inst: Instrument) -> Order:
        from alpaca.trading.enums import OrderSide, TimeInForce as ATIF
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest, StopOrderRequest

        side = OrderSide.BUY if req.side == Side.BUY else OrderSide.SELL
        tif_map = {TimeInForce.DAY: ATIF.DAY, TimeInForce.GTC: ATIF.GTC, TimeInForce.IOC: ATIF.IOC}
        tif = tif_map[req.tif]
        if inst.market == Market.CRYPTO and tif == ATIF.DAY:
            tif = ATIF.GTC  # Alpaca crypto only accepts gtc / ioc
        common = dict(symbol=self._sym(inst), qty=req.qty, side=side, time_in_force=tif, client_order_id=req.client_order_id)
        if req.order_type == OrderType.MARKET:
            areq = MarketOrderRequest(**common)
        elif req.order_type == OrderType.LIMIT:
            areq = LimitOrderRequest(limit_price=req.limit_price, **common)
        elif req.order_type == OrderType.STOP:
            areq = StopOrderRequest(stop_price=req.stop_price, **common)
        else:
            areq = StopLimitOrderRequest(stop_price=req.stop_price, limit_price=req.limit_price, **common)

        order = Order(id=self.store.new_id(), venue=self.name, symbol=inst.symbol, market=inst.market, currency="USD",
                      side=req.side, qty=req.qty, order_type=req.order_type, limit_price=req.limit_price,
                      stop_price=req.stop_price, tif=req.tif, reason=req.reason, strategy=req.strategy,
                      client_order_id=req.client_order_id)
        try:
            ao = self.client.submit_order(areq)
        except Exception as e:  # noqa: BLE001
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)[:500]
            self.store.save_order(order)
            raise BrokerError(f"alpaca rejected order: {e}", details={"order_id": order.id}) from e
        self._apply(order, ao)
        self.store.save_order(order)
        return order

    def _apply(self, order: Order, ao) -> None:
        order.venue_order_id = str(ao.id)
        order.status = STATUS_MAP.get(str(ao.status.value if hasattr(ao.status, "value") else ao.status), OrderStatus.ACCEPTED)
        order.filled_qty = float(ao.filled_qty or 0)
        order.avg_fill_price = float(ao.filled_avg_price) if ao.filled_avg_price else None
        order.updated_at = utcnow()

    def refresh_order(self, order: Order) -> Order:
        if order.venue_order_id and order.status.is_open:
            ao = self.client.get_order_by_id(order.venue_order_id)
            self._apply(order, ao)
            self.store.save_order(order)
        return order

    def cancel_order(self, order: Order) -> Order:
        if not order.venue_order_id:
            raise BrokerError("order has no venue id")
        self.client.cancel_order_by_id(order.venue_order_id)
        return self.refresh_order(order)

    def sync(self) -> dict:
        n = 0
        for o in self.store.list_orders(venue=self.name, open_only=True, limit=1000):
            self.refresh_order(o)
            n += 1
        return {"refreshed": n}
