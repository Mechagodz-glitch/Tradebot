"""Pre-trade risk checks. Every order passes through ``RiskEngine.check`` before reaching a venue."""

from __future__ import annotations

from typing import Optional

from .errors import RiskRejected
from .hours import market_session
from .models import Account, Instrument, OrderRequest, OrderType, Position, Quote, Side


class RiskEngine:
    def __init__(self, settings, store):
        self.settings = settings
        self.cfg = settings.risk
        self.store = store

    def kill_switch_active(self) -> bool:
        return self.settings.resolve(self.cfg.kill_switch_file).exists()

    def reference_price(self, req: OrderRequest, quote: Quote) -> float:
        if req.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and req.limit_price:
            return req.limit_price
        if req.order_type == OrderType.STOP and req.stop_price:
            return req.stop_price
        return quote.last

    def check(self, req: OrderRequest, inst: Instrument, quote: Quote, account: Optional[Account],
              position: Optional[Position], venue_live: bool, open_orders: int) -> dict:
        """Raise RiskRejected on failure; return a summary dict on success."""
        cfg = self.cfg
        ccy = inst.currency
        if self.kill_switch_active():
            raise RiskRejected("kill switch is active (remove the kill file or run `tradebot kill --off`)", code="kill_switch")
        if venue_live and not self.settings.live_trading_enabled:
            raise RiskRejected("live trading is disabled; set live_trading_enabled: true in config.yaml or TRADEBOT_LIVE=1",
                               code="live_trading_disabled")
        if inst.market.value not in cfg.allowed_markets:
            raise RiskRejected(f"market {inst.market.value} is not in allowed_markets", code="market_not_allowed")
        allowed = cfg.allowed_symbols
        if isinstance(allowed, dict):
            allowed = allowed.get(inst.market.value)
        if allowed is not None and inst.symbol not in {s.upper() for s in allowed}:
            raise RiskRejected(f"{inst.symbol} is not in allowed_symbols for market {inst.market.value}", code="symbol_not_allowed")
        if venue_live and not cfg.allow_outside_hours:
            sess = market_session(inst.market)
            if not sess["open"]:
                raise RiskRejected(f"{inst.market.value} market is closed ({sess['detail']}); set risk.allow_outside_hours to override",
                                   code="market_closed", details=sess)
        if inst.symbol in {s.upper() for s in cfg.blocked_symbols}:
            raise RiskRejected(f"{inst.symbol} is blocked", code="symbol_blocked")
        if open_orders >= cfg.max_open_orders:
            raise RiskRejected(f"{open_orders} open orders >= max_open_orders {cfg.max_open_orders}", code="too_many_open_orders")
        recent = self.store.orders_in_last(60, venue=req.venue)
        if recent >= cfg.max_orders_per_minute:
            raise RiskRejected(f"{recent} orders in the last minute >= max_orders_per_minute {cfg.max_orders_per_minute}",
                               code="rate_limited")

        px = self.reference_price(req, quote)
        notional = req.qty * px
        max_order = cfg.max_order_notional.get(ccy)
        if max_order is not None and notional > max_order:
            raise RiskRejected(f"order notional {notional:,.2f} {ccy} exceeds max_order_notional {max_order:,.2f}",
                               code="order_too_large", details={"notional": notional, "limit": max_order})

        cur_qty = position.qty if position else 0.0
        signed = req.qty if req.side == Side.BUY else -req.qty
        new_qty = cur_qty + signed
        reduces = abs(new_qty) < abs(cur_qty) - 1e-12
        max_pos = cfg.max_position_notional.get(ccy)
        if max_pos is not None and not reduces and abs(new_qty) * px > max_pos:
            raise RiskRejected(f"resulting position {abs(new_qty) * px:,.2f} {ccy} exceeds max_position_notional {max_pos:,.2f}",
                               code="position_too_large", details={"resulting_notional": abs(new_qty) * px, "limit": max_pos})

        max_loss = cfg.max_daily_loss.get(ccy)
        if max_loss is not None and account is not None and not reduces:
            start = self.store.equity_at_day_start(req.venue, inst.market)
            if start is not None and account.equity and start - account.equity >= max_loss:
                raise RiskRejected(f"daily loss {start - account.equity:,.2f} {ccy} >= max_daily_loss {max_loss:,.2f}; "
                                   f"only position-reducing orders allowed", code="daily_loss_limit")
        return {"notional": notional, "reference_price": px, "reduces_position": reduces}
