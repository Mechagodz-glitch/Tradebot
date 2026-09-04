"""Paper broker: deterministic simulator persisted in SQLite, fills at live quotes."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..errors import BrokerError, NotFound
from ..models import (
    MARKET_CURRENCY,
    Account,
    EquityPoint,
    Fill,
    Instrument,
    JournalEntry,
    Market,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
    TimeInForce,
    utcnow,
)
from ..symbols import parse_symbol
from .base import Broker

EPS = 1e-12


def apply_fill(pos_qty: float, pos_avg: float, side: Side, qty: float, price: float) -> tuple[float, float, float]:
    """Average-cost position accounting. Returns (new_qty, new_avg, realized_pnl)."""
    signed = qty if side == Side.BUY else -qty
    realized = 0.0
    if pos_qty == 0 or (pos_qty > 0) == (signed > 0):
        new_qty = pos_qty + signed
        new_avg = (abs(pos_qty) * pos_avg + qty * price) / abs(new_qty) if abs(new_qty) > EPS else 0.0
        return new_qty, new_avg, realized
    # reducing or flipping
    closing = min(abs(pos_qty), qty)
    direction = 1.0 if pos_qty > 0 else -1.0
    realized = (price - pos_avg) * closing * direction
    new_qty = pos_qty + signed
    if abs(new_qty) <= EPS:
        return 0.0, 0.0, realized
    if (new_qty > 0) == (pos_qty > 0):
        return new_qty, pos_avg, realized  # partial reduce, avg unchanged
    return new_qty, price, realized  # flipped: remainder opened at fill price


class PaperBroker(Broker):
    name = "paper"
    live = False
    markets = (Market.US, Market.IN, Market.CRYPTO)

    # ---- accounts ---------------------------------------------------------
    def account(self, market: Market, mark: bool = True) -> Account:
        acct = self.store.get_account(self.name, market)
        if acct is None:
            cash = float(self.settings.paper.starting_cash.get(market.value, 100_000))
            acct = Account(venue=self.name, market=market, currency=MARKET_CURRENCY[market], cash=cash, starting_cash=cash)
            self.store.upsert_account(acct)
        pos_value = 0.0
        unreal = 0.0
        if mark:
            for p in self.positions(market, mark=True):
                pos_value += p.market_value or 0.0
                unreal += p.unrealized_pnl or 0.0
        acct.positions_value = pos_value
        acct.unrealized_pnl = unreal
        acct.equity = acct.cash + pos_value
        acct.buying_power = acct.cash
        acct.ts = utcnow()
        return acct

    def positions(self, market: Optional[Market] = None, mark: bool = True) -> list[Position]:
        out = self.store.list_positions(self.name, market)
        if mark:
            for p in out:
                try:
                    q = self.data.quote(parse_symbol(p.symbol, p.market))
                    p.market_price = q.last
                    p.market_value = p.qty * q.last
                    p.unrealized_pnl = (q.last - p.avg_price) * p.qty
                except Exception:  # noqa: BLE001
                    p.market_price = None
        return out

    # ---- orders -----------------------------------------------------------
    def place_order(self, req: OrderRequest, inst: Instrument) -> Order:
        order = Order(id=self.store.new_id(), venue=self.name, symbol=inst.symbol, market=inst.market, currency=inst.currency,
                      side=req.side, qty=req.qty, order_type=req.order_type, limit_price=req.limit_price,
                      stop_price=req.stop_price, tif=req.tif, status=OrderStatus.ACCEPTED, reason=req.reason,
                      strategy=req.strategy, client_order_id=req.client_order_id)
        self.store.save_order(order)
        quote = self.data.quote(inst)
        self._try_fill(order, quote)
        if order.status.is_open and order.tif == TimeInForce.IOC:
            order.status = OrderStatus.CANCELED
            order.reject_reason = "ioc_not_marketable"
            self.store.save_order(order)
        return order

    def cancel_order(self, order: Order) -> Order:
        if not order.status.is_open:
            raise BrokerError(f"order {order.id} is {order.status.value}; cannot cancel", code="not_open")
        order.status = OrderStatus.CANCELED
        self.store.save_order(order)
        self.store.journal(JournalEntry(kind="order", venue=self.name, symbol=order.symbol, order_id=order.id,
                                        text=f"canceled {order.side.value} {order.qty} {order.symbol}"))
        return order

    def sync(self) -> dict:
        filled, expired, checked = 0, 0, 0
        day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        for order in self.store.list_orders(venue=self.name, open_only=True, limit=10_000):
            checked += 1
            if order.tif == TimeInForce.DAY and order.created_at < day_start - timedelta(hours=0):
                if order.created_at.date() < utcnow().date():
                    order.status = OrderStatus.EXPIRED
                    self.store.save_order(order)
                    expired += 1
                    continue
            inst = parse_symbol(order.symbol, order.market)
            try:
                quote = self.data.quote(inst)
            except Exception:  # noqa: BLE001
                continue
            self._try_fill(order, quote)
            if order.status == OrderStatus.FILLED:
                filled += 1
        snaps = {}
        for m in Market:
            acct = self.store.get_account(self.name, m)
            if acct is None and not self.store.list_positions(self.name, m, include_flat=True):
                continue
            a = self.account(m)
            self.store.add_equity_point(EquityPoint(venue=self.name, market=m, ts=utcnow(), cash=a.cash,
                                                     positions_value=a.positions_value, equity=a.equity))
            snaps[m.value] = a.equity
        return {"checked": checked, "filled": filled, "expired": expired, "equity": snaps}

    def reset(self, market: Optional[Market] = None) -> None:
        if market is None:
            self.store.reset_paper(self.name)
            return
        # market-scoped reset: remove that market's rows only
        with self.store.session() as s:
            from ..store import AccountRow, EquityRow, FillRow, OrderRow, PositionRow
            for tbl in (OrderRow, FillRow, PositionRow, AccountRow, EquityRow):
                s.execute(tbl.__table__.delete().where(tbl.venue == self.name, tbl.market == market.value))
            s.commit()

    # ---- fill engine ------------------------------------------------------
    def _fill_price(self, order: Order, q: Quote) -> Optional[float]:
        buy = order.side == Side.BUY
        ref = (q.ask if buy else q.bid) or q.last
        if ref is None or ref <= 0:
            return None
        slip = self.settings.paper.slippage_bps / 10_000.0
        aggressive = ref * (1 + slip) if buy else ref * (1 - slip)
        t = order.order_type
        if t == OrderType.MARKET:
            return aggressive
        if t == OrderType.LIMIT:
            if (buy and ref <= order.limit_price) or (not buy and ref >= order.limit_price):
                return ref
            return None
        triggered = (buy and q.last >= order.stop_price) or (not buy and q.last <= order.stop_price)
        if not triggered:
            return None
        if t == OrderType.STOP:
            return aggressive
        # stop limit
        if (buy and ref <= order.limit_price) or (not buy and ref >= order.limit_price):
            return ref
        return None

    def _try_fill(self, order: Order, q: Quote) -> None:
        price = self._fill_price(order, q)
        if price is None:
            return
        qty = order.remaining_qty
        notional = qty * price
        fee = notional * float(self.settings.paper.fee_bps.get(order.market.value, 0.0)) / 10_000.0
        acct = self.store.get_account(self.name, order.market) or self.account(order.market, mark=False)
        pos = self.store.get_position(self.name, order.symbol) or Position(
            venue=self.name, symbol=order.symbol, market=order.market, currency=order.currency, qty=0.0, avg_price=0.0)

        if order.side == Side.BUY and notional + fee > acct.cash + EPS:
            self._reject(order, f"insufficient_funds: need {notional + fee:.2f} {order.currency}, cash {acct.cash:.2f}")
            return
        if order.side == Side.SELL and not self.settings.paper.allow_short and qty > pos.qty + EPS:
            self._reject(order, f"insufficient_position: have {pos.qty}, sell {qty} (shorting disabled)")
            return

        new_qty, new_avg, realized = apply_fill(pos.qty, pos.avg_price, order.side, qty, price)
        pos.qty, pos.avg_price = new_qty, new_avg
        pos.realized_pnl += realized
        self.store.upsert_position(pos)

        acct.cash += (-notional if order.side == Side.BUY else notional) - fee
        acct.realized_pnl += realized - fee
        self.store.upsert_account(acct)

        fill = Fill(id=self.store.new_id(), order_id=order.id, venue=self.name, symbol=order.symbol, market=order.market,
                    side=order.side, qty=qty, price=price, fee=fee)
        self.store.save_fill(fill)

        prev_filled = order.filled_qty
        order.avg_fill_price = ((order.avg_fill_price or 0.0) * prev_filled + price * qty) / (prev_filled + qty)
        order.filled_qty = prev_filled + qty
        order.fees += fee
        order.status = OrderStatus.FILLED
        self.store.save_order(order)
        self.store.journal(JournalEntry(kind="fill", venue=self.name, symbol=order.symbol, order_id=order.id,
                                        text=f"filled {order.side.value} {qty:g} {order.symbol} @ {price:.4f} {order.currency} (fee {fee:.2f})",
                                        data={"qty": qty, "price": price, "fee": fee, "realized": realized, "source": q.source}))

    def _reject(self, order: Order, reason: str) -> None:
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        self.store.save_order(order)
        self.store.journal(JournalEntry(kind="order", venue=self.name, symbol=order.symbol, order_id=order.id,
                                        text=f"rejected {order.side.value} {order.qty:g} {order.symbol}: {reason}"))

    def _check(self) -> str:
        n = len(self.store.list_orders(venue=self.name, limit=10_000))
        return f"sqlite ok, {n} paper orders on record"
