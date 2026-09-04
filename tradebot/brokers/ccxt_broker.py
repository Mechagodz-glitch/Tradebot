"""Generic crypto exchange execution through CCXT (Kraken, Coinbase, OKX, KuCoin, Delta, CoinDCX...).

Exchange id comes from ``ccxt.exchange`` in config; keys from CCXT_API_KEY / CCXT_SECRET / CCXT_PASSWORD.
Set ``ccxt.sandbox: true`` for exchanges that offer a test environment."""

from __future__ import annotations

from typing import Optional

from ..data.ccxt_data import make_exchange
from ..errors import BrokerError
from ..models import (
    Account, Instrument, Market, Order, OrderRequest, OrderStatus, OrderType, Position, Side, TimeInForce, utcnow,
)
from .base import Broker

STATUS_MAP = {"open": OrderStatus.ACCEPTED, "closed": OrderStatus.FILLED, "canceled": OrderStatus.CANCELED,
              "cancelled": OrderStatus.CANCELED, "expired": OrderStatus.EXPIRED, "rejected": OrderStatus.REJECTED}
QUOTE_CCYS = ("USD", "USDT", "USDC", "INR", "EUR")


class CcxtBroker(Broker):
    name = "ccxt"
    live = True
    markets = (Market.CRYPTO,)

    def __init__(self, settings, store, data):
        super().__init__(settings, store, data)
        self.exchange_id = settings.ccxt.exchange
        self.live = not settings.ccxt.sandbox
        self._ex = None

    def available(self) -> bool:
        return bool(self.settings.ccxt_api_key and self.settings.ccxt_secret)

    @property
    def ex(self):
        if self._ex is None:
            if not self.available():
                raise BrokerError(f"ccxt/{self.exchange_id}: CCXT_API_KEY / CCXT_SECRET not set", code="credentials_missing")
            self._ex = make_exchange(self.exchange_id, self.settings.ccxt_api_key, self.settings.ccxt_secret,
                                     self.settings.ccxt_password, sandbox=self.settings.ccxt.sandbox,
                                     default_type=self.settings.ccxt.default_type)
        return self._ex

    @staticmethod
    def _sym(inst: Instrument) -> str:
        return f"{inst.base}/{inst.currency}"

    def _check(self) -> str:
        b = self.ex.fetch_balance()
        tot = {k: v for k, v in (b.get("total") or {}).items() if v}
        return f"{'SANDBOX' if self.settings.ccxt.sandbox else 'LIVE'} {self.exchange_id} balances={tot}"

    def account(self, market: Market) -> Account:
        b = self.ex.fetch_balance()
        total = b.get("total") or {}
        free = b.get("free") or {}
        ccy = next((c for c in QUOTE_CCYS if total.get(c)), "USD")
        positions = self.positions(market)
        pv = sum(p.market_value or 0.0 for p in positions)
        cash = float(total.get(ccy) or 0.0)
        return Account(venue=self.name, market=Market.CRYPTO, currency=ccy, cash=cash, positions_value=pv, equity=cash + pv,
                       buying_power=float(free.get(ccy) or 0.0))

    def positions(self, market: Optional[Market] = None, mark: bool = True) -> list[Position]:
        b = self.ex.fetch_balance()
        total = b.get("total") or {}
        ccy = next((c for c in QUOTE_CCYS if total.get(c)), "USD")
        out = []
        for asset, qty in total.items():
            if not qty or asset in QUOTE_CCYS:
                continue
            sym = f"{asset}-{ccy}"
            price = None
            if mark:
                try:
                    price = float(self.ex.fetch_ticker(f"{asset}/{ccy}")["last"])
                except Exception:  # noqa: BLE001
                    price = None
            out.append(Position(venue=self.name, symbol=sym, market=Market.CRYPTO, currency=ccy, qty=float(qty),
                                avg_price=0.0, market_price=price, market_value=(price or 0.0) * float(qty)))
        return out

    def place_order(self, req: OrderRequest, inst: Instrument) -> Order:
        order = Order(id=self.store.new_id(), venue=self.name, symbol=inst.symbol, market=Market.CRYPTO, currency=inst.currency,
                      side=req.side, qty=req.qty, order_type=req.order_type, limit_price=req.limit_price,
                      stop_price=req.stop_price, tif=req.tif, reason=req.reason, strategy=req.strategy,
                      client_order_id=req.client_order_id)
        params: dict = {}
        if req.tif == TimeInForce.IOC:
            params["timeInForce"] = "IOC"
        if req.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            params["triggerPrice"] = req.stop_price
        ctype = "market" if req.order_type in (OrderType.MARKET, OrderType.STOP) else "limit"
        price = req.limit_price if ctype == "limit" else None
        try:
            res = self.ex.create_order(self._sym(inst), ctype, req.side.value, req.qty, price, params)
        except Exception as e:  # noqa: BLE001
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)[:500]
            self.store.save_order(order)
            raise BrokerError(f"{self.exchange_id} rejected order: {e}", details={"order_id": order.id}) from e
        self._apply(order, res)
        self.store.save_order(order)
        return order

    def _apply(self, order: Order, res: dict) -> None:
        order.venue_order_id = str(res.get("id"))
        st = res.get("status")
        order.status = STATUS_MAP.get(st, OrderStatus.ACCEPTED) if st else OrderStatus.ACCEPTED
        order.filled_qty = float(res.get("filled") or 0.0)
        order.avg_fill_price = float(res["average"]) if res.get("average") else None
        fee = res.get("fee") or {}
        if fee.get("cost"):
            order.fees = float(fee["cost"])
        order.updated_at = utcnow()

    def refresh_order(self, order: Order) -> Order:
        if order.venue_order_id and order.status.is_open:
            res = self.ex.fetch_order(order.venue_order_id, order.symbol.replace("-", "/"))
            self._apply(order, res)
            self.store.save_order(order)
        return order

    def cancel_order(self, order: Order) -> Order:
        if not order.venue_order_id:
            raise BrokerError("order has no venue id")
        self.ex.cancel_order(order.venue_order_id, order.symbol.replace("-", "/"))
        return self.refresh_order(order)

    def sync(self) -> dict:
        n = 0
        for o in self.store.list_orders(venue=self.name, open_only=True, limit=1000):
            try:
                self.refresh_order(o)
                n += 1
            except Exception:  # noqa: BLE001
                pass
        return {"refreshed": n}
