"""Zerodha Kite Connect (Indian equities). Requires a Kite Connect app (API key + secret) and a daily
access token obtained through the login flow (``tradebot kite-login``)."""

from __future__ import annotations

from typing import Optional

from ..errors import BrokerError
from ..models import (
    Account, Instrument, Market, Order, OrderRequest, OrderStatus, OrderType, Position, Side, TimeInForce, utcnow,
)
from .base import Broker

STATUS_MAP = {
    "COMPLETE": OrderStatus.FILLED, "REJECTED": OrderStatus.REJECTED, "CANCELLED": OrderStatus.CANCELED,
    "OPEN": OrderStatus.ACCEPTED, "TRIGGER PENDING": OrderStatus.ACCEPTED, "PUT ORDER REQ RECEIVED": OrderStatus.NEW,
    "VALIDATION PENDING": OrderStatus.NEW, "OPEN PENDING": OrderStatus.NEW, "MODIFY PENDING": OrderStatus.ACCEPTED,
    "CANCEL PENDING": OrderStatus.ACCEPTED, "AMO REQ RECEIVED": OrderStatus.ACCEPTED,
}


class KiteBroker(Broker):
    name = "kite"
    live = True
    markets = (Market.IN,)

    def __init__(self, settings, store, data):
        super().__init__(settings, store, data)
        self._kite = None

    def available(self) -> bool:
        return bool(self.settings.kite_api_key and self.settings.kite_access_token)

    @property
    def kite(self):
        if self._kite is None:
            from kiteconnect import KiteConnect
            if not self.settings.kite_api_key:
                raise BrokerError("kite: KITE_API_KEY not set", code="credentials_missing")
            k = KiteConnect(api_key=self.settings.kite_api_key)
            if not self.settings.kite_access_token:
                raise BrokerError("kite: KITE_ACCESS_TOKEN not set; run `tradebot kite-login`", code="credentials_missing")
            k.set_access_token(self.settings.kite_access_token)
            self._kite = k
        return self._kite

    def login_url(self) -> str:
        from kiteconnect import KiteConnect
        if not self.settings.kite_api_key:
            raise BrokerError("kite: KITE_API_KEY not set", code="credentials_missing")
        return KiteConnect(api_key=self.settings.kite_api_key).login_url()

    def exchange_token(self, request_token: str) -> str:
        from kiteconnect import KiteConnect
        if not (self.settings.kite_api_key and self.settings.kite_api_secret):
            raise BrokerError("kite: KITE_API_KEY / KITE_API_SECRET not set", code="credentials_missing")
        k = KiteConnect(api_key=self.settings.kite_api_key)
        data = k.generate_session(request_token, api_secret=self.settings.kite_api_secret)
        return data["access_token"]

    def _check(self) -> str:
        p = self.kite.profile()
        return f"LIVE user {p.get('user_id')} ({p.get('user_name')}) broker={p.get('broker')}"

    def account(self, market: Market) -> Account:
        m = self.kite.margins("equity")
        cash = float(m.get("net", 0.0))
        avail = float((m.get("available") or {}).get("cash", cash))
        positions = self.positions(market, mark=False)
        pv = sum(p.market_value or 0.0 for p in positions)
        return Account(venue=self.name, market=Market.IN, currency="INR", cash=cash, positions_value=pv, equity=cash + pv,
                       buying_power=avail, unrealized_pnl=sum(p.unrealized_pnl or 0.0 for p in positions))

    def positions(self, market: Optional[Market] = None, mark: bool = True) -> list[Position]:
        out: dict[str, Position] = {}
        for h in self.kite.holdings():
            qty = float(h.get("quantity", 0)) + float(h.get("t1_quantity", 0))
            if qty == 0:
                continue
            sym = f"{h['exchange']}:{h['tradingsymbol']}"
            lp = float(h.get("last_price") or 0)
            out[sym] = Position(venue=self.name, symbol=sym, market=Market.IN, currency="INR", qty=qty,
                                avg_price=float(h.get("average_price") or 0), market_price=lp or None,
                                market_value=qty * lp, unrealized_pnl=float(h.get("pnl") or 0))
        for p in self.kite.positions().get("net", []):
            qty = float(p.get("quantity", 0))
            if qty == 0:
                continue
            sym = f"{p['exchange']}:{p['tradingsymbol']}"
            lp = float(p.get("last_price") or 0)
            pos = Position(venue=self.name, symbol=sym, market=Market.IN, currency="INR", qty=qty,
                           avg_price=float(p.get("average_price") or 0), market_price=lp or None, market_value=qty * lp,
                           unrealized_pnl=float(p.get("unrealised") or p.get("pnl") or 0))
            if sym in out:  # intraday position on top of holdings: merge quantities
                base = out[sym]
                tot = base.qty + qty
                base.avg_price = (base.avg_price * base.qty + pos.avg_price * qty) / tot if tot else 0
                base.qty = tot
                base.market_value = tot * lp
                base.unrealized_pnl = (base.unrealized_pnl or 0) + (pos.unrealized_pnl or 0)
            else:
                out[sym] = pos
        return list(out.values())

    def place_order(self, req: OrderRequest, inst: Instrument) -> Order:
        k = self.kite
        otype = {OrderType.MARKET: k.ORDER_TYPE_MARKET, OrderType.LIMIT: k.ORDER_TYPE_LIMIT,
                 OrderType.STOP: k.ORDER_TYPE_SLM, OrderType.STOP_LIMIT: k.ORDER_TYPE_SL}[req.order_type]
        validity = k.VALIDITY_IOC if req.tif == TimeInForce.IOC else k.VALIDITY_DAY
        if req.qty != int(req.qty):
            raise BrokerError("kite: quantity must be a whole number of shares", code="invalid_qty")
        order = Order(id=self.store.new_id(), venue=self.name, symbol=inst.symbol, market=Market.IN, currency="INR",
                      side=req.side, qty=req.qty, order_type=req.order_type, limit_price=req.limit_price,
                      stop_price=req.stop_price, tif=req.tif, reason=req.reason, strategy=req.strategy,
                      client_order_id=req.client_order_id)
        try:
            oid = k.place_order(
                variety=k.VARIETY_REGULAR, exchange=inst.exchange or self.settings.kite.exchange, tradingsymbol=inst.base,
                transaction_type=k.TRANSACTION_TYPE_BUY if req.side == Side.BUY else k.TRANSACTION_TYPE_SELL,
                quantity=int(req.qty), product=self.settings.kite.product, order_type=otype,
                price=req.limit_price if req.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else None,
                trigger_price=req.stop_price if req.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) else None,
                validity=validity, tag=(req.strategy or "tradebot")[:20],
            )
        except Exception as e:  # noqa: BLE001
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)[:500]
            self.store.save_order(order)
            raise BrokerError(f"kite rejected order: {e}", details={"order_id": order.id}) from e
        order.venue_order_id = str(oid)
        order.status = OrderStatus.ACCEPTED
        self.store.save_order(order)
        try:
            self.refresh_order(order)
        except Exception:  # noqa: BLE001
            pass
        return order

    def refresh_order(self, order: Order) -> Order:
        if not order.venue_order_id or not order.status.is_open:
            return order
        hist = self.kite.order_history(order.venue_order_id)
        if hist:
            last = hist[-1]
            order.status = STATUS_MAP.get(last.get("status", ""), order.status)
            order.filled_qty = float(last.get("filled_quantity") or 0)
            order.avg_fill_price = float(last.get("average_price") or 0) or None
            if order.status == OrderStatus.REJECTED:
                order.reject_reason = last.get("status_message")
            order.updated_at = utcnow()
            self.store.save_order(order)
        return order

    def cancel_order(self, order: Order) -> Order:
        if not order.venue_order_id:
            raise BrokerError("order has no venue id")
        self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order.venue_order_id)
        return self.refresh_order(order)

    def sync(self) -> dict:
        n = 0
        for o in self.store.list_orders(venue=self.name, open_only=True, limit=1000):
            self.refresh_order(o)
            n += 1
        return {"refreshed": n}
