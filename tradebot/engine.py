"""TradingEngine: the single entry point used by the CLI and the HTTP API."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from . import __version__
from .brokers import BrokerRegistry
from .config import Settings, load_settings
from .data import MarketData
from .errors import BrokerError, NotFound, RiskRejected
from .hours import market_session
from .models import (
    Account, Candle, CheckResult, Instrument, JournalEntry, Market, Order, OrderRequest, OrderStatus, Position, Quote, Side, utcnow,
)
from .risk import RiskEngine
from .store import Store
from .symbols import parse_symbol


class TradingEngine:
    def __init__(self, settings: Optional[Settings] = None, store: Optional[Store] = None, data: Optional[MarketData] = None):
        self.settings = settings or load_settings()
        self.store = store or Store(self.settings.resolve(self.settings.db_path))
        self.data = data or MarketData(self.settings)
        self.brokers = BrokerRegistry(self.settings, self.store, self.data)
        self.risk = RiskEngine(self.settings, self.store)

    # ---- helpers ------------------------------------------------------------
    def instrument(self, symbol: str, market: Optional[Market] = None) -> Instrument:
        return parse_symbol(symbol, market)

    def venue(self, name: Optional[str] = None):
        return self.brokers.get(name or self.settings.default_venue)

    # ---- data ---------------------------------------------------------------
    def quote(self, symbol: str, market: Optional[Market] = None, provider: Optional[str] = None) -> Quote:
        return self.data.quote(self.instrument(symbol, market), provider=provider, use_cache=False)

    def quotes(self, symbols: list[str], market: Optional[Market] = None) -> list[dict]:
        out = []
        for s in symbols:
            try:
                out.append(self.quote(s, market).model_dump(mode="json"))
            except Exception as e:  # noqa: BLE001
                out.append({"symbol": s.upper(), "error": str(e)})
        return out

    def candles(self, symbol: str, interval: str = "1d", limit: int = 100, market: Optional[Market] = None,
                start: Optional[datetime] = None, end: Optional[datetime] = None, provider: Optional[str] = None) -> tuple[list[Candle], str]:
        return self.data.candles(self.instrument(symbol, market), interval=interval, limit=limit, start=start, end=end, provider=provider)

    def search(self, text: str, market: Market = Market.IN) -> list[dict]:
        if market == Market.IN:
            return self.data.provider("upstox").search(text)
        raise NotFound("symbol search is only implemented for the Indian market (Upstox instrument master)")

    # ---- account state ------------------------------------------------------
    def account(self, market: Market, venue: Optional[str] = None) -> Account:
        return self.venue(venue).account(market)

    def accounts(self, venue: Optional[str] = None) -> list[Account]:
        b = self.venue(venue)
        out = []
        for m in b.markets:
            try:
                out.append(b.account(m))
            except Exception as e:  # noqa: BLE001
                out.append(Account(venue=b.name, market=m, currency="?", cash=0.0, equity=0.0).model_copy(update={"error": str(e)}))
        return out

    def positions(self, venue: Optional[str] = None, market: Optional[Market] = None) -> list[Position]:
        return self.venue(venue).positions(market)

    def orders(self, venue: Optional[str] = None, symbol: Optional[str] = None, open_only: bool = False, limit: int = 50,
               status: Optional[list[str]] = None, refresh: bool = False) -> list[Order]:
        sym = self.instrument(symbol).symbol if symbol else None
        rows = self.store.list_orders(venue=venue, symbol=sym, open_only=open_only, limit=limit, status=status)
        if refresh and venue:
            b = self.venue(venue)
            rows = [b.refresh_order(o) if o.status.is_open else o for o in rows]
        return rows

    def order(self, order_id: str, refresh: bool = True) -> Order:
        o = self.store.get_order(order_id)
        if not o:
            raise NotFound(f"order {order_id} not found")
        if refresh and o.status.is_open:
            try:
                o = self.venue(o.venue).refresh_order(o)
            except Exception:  # noqa: BLE001
                pass
        return o

    # ---- trading ------------------------------------------------------------
    def place_order(self, req: OrderRequest, dry_run: bool = False) -> Order:
        req.validate_prices()
        venue_name = req.venue or self.settings.default_venue
        broker = self.venue(venue_name)
        req.venue = broker.name
        inst = self.instrument(req.symbol, req.market)
        req.symbol = inst.symbol
        if not broker.supports(inst.market):
            raise BrokerError(f"venue {broker.name} does not support market {inst.market.value}", code="market_unsupported")
        if not broker.available():
            raise BrokerError(f"venue {broker.name} is not configured (missing credentials)", code="credentials_missing")

        quote = self.data.quote(inst, use_cache=False)
        account = None
        position = None
        try:
            account = broker.account(inst.market)
        except Exception:  # noqa: BLE001
            pass
        try:
            position = next((p for p in broker.positions(inst.market, mark=False) if p.symbol == inst.symbol), None)
        except Exception:  # noqa: BLE001
            pass
        open_orders = len(self.store.list_orders(venue=broker.name, open_only=True, limit=10_000))
        try:
            summary = self.risk.check(req, inst, quote, account, position, broker.live, open_orders)
        except RiskRejected as e:
            self.store.journal(JournalEntry(kind="risk", venue=broker.name, symbol=inst.symbol,
                                            text=f"REJECTED {req.side.value} {req.qty:g} {inst.symbol}: {e.message}",
                                            data={"code": e.code, "reason": req.reason}))
            raise

        if dry_run:
            return Order(id="dry-run", venue=broker.name, symbol=inst.symbol, market=inst.market, currency=inst.currency,
                         side=req.side, qty=req.qty, order_type=req.order_type, limit_price=req.limit_price,
                         stop_price=req.stop_price, tif=req.tif, status=OrderStatus.NEW, reason=req.reason,
                         reject_reason=f"dry_run ok: notional {summary['notional']:.2f} {inst.currency} @ ref {summary['reference_price']}")

        order = broker.place_order(req, inst)
        self.store.journal(JournalEntry(kind="order", venue=broker.name, symbol=inst.symbol, order_id=order.id,
                                        text=f"{order.status.value}: {req.side.value} {req.qty:g} {inst.symbol} {req.order_type.value}"
                                             + (f" @ {req.limit_price}" if req.limit_price else "")
                                             + (f" | reason: {req.reason}" if req.reason else ""),
                                        data={"reason": req.reason, "strategy": req.strategy, "risk": summary, "live": broker.live}))
        return order

    def cancel_order(self, order_id: str) -> Order:
        o = self.order(order_id, refresh=False)
        return self.venue(o.venue).cancel_order(o)

    def cancel_all(self, venue: Optional[str] = None, symbol: Optional[str] = None) -> list[Order]:
        out = []
        for o in self.orders(venue=venue, symbol=symbol, open_only=True, limit=10_000):
            try:
                out.append(self.venue(o.venue).cancel_order(o))
            except Exception:  # noqa: BLE001
                pass
        return out

    def close_position(self, symbol: str, venue: Optional[str] = None, market: Optional[Market] = None,
                       reason: Optional[str] = None) -> Order:
        inst = self.instrument(symbol, market)
        broker = self.venue(venue)
        pos = next((p for p in broker.positions(inst.market, mark=False) if p.symbol == inst.symbol), None)
        if not pos or abs(pos.qty) < 1e-12:
            raise NotFound(f"no open position in {inst.symbol} on {broker.name}")
        side = Side.SELL if pos.qty > 0 else Side.BUY
        return self.place_order(OrderRequest(symbol=inst.symbol, side=side, qty=abs(pos.qty), venue=broker.name,
                                             market=inst.market, reason=reason or "close position", tif="gtc" if inst.market == Market.CRYPTO else "day"))

    def sync(self, venue: Optional[str] = None) -> dict:
        names = [venue] if venue else [n for n in self.brokers.names() if self.brokers.get(n).available()]
        out = {}
        for n in names:
            try:
                out[n] = self.brokers.get(n).sync()
            except Exception as e:  # noqa: BLE001
                out[n] = {"error": str(e)}
        return out

    # ---- reporting ------------------------------------------------------------
    def pnl(self, venue: Optional[str] = None, market: Optional[Market] = None) -> list[dict]:
        b = self.venue(venue)
        markets = [market] if market else list(b.markets)
        out = []
        for m in markets:
            try:
                a = b.account(m)
            except Exception as e:  # noqa: BLE001
                out.append({"venue": b.name, "market": m.value, "error": str(e)})
                continue
            day_start = self.store.equity_at_day_start(b.name, m)
            positions = [p for p in b.positions(m) if abs(p.qty) > 1e-12]
            out.append({
                "venue": b.name, "market": m.value, "currency": a.currency, "cash": a.cash,
                "positions_value": a.positions_value, "equity": a.equity, "starting_cash": a.starting_cash,
                "realized_pnl": a.realized_pnl, "unrealized_pnl": a.unrealized_pnl,
                "total_pnl": (a.equity - a.starting_cash) if a.starting_cash else None,
                "day_pnl": (a.equity - day_start) if day_start is not None else None,
                "open_positions": len(positions),
            })
        return out

    def equity_curve(self, venue: str = "paper", market: Market = Market.CRYPTO, limit: int = 2000):
        return self.store.equity_curve(venue, market, limit)

    def journal(self, limit: int = 50, kind: Optional[str] = None, symbol: Optional[str] = None) -> list[JournalEntry]:
        return self.store.list_journal(limit=limit, kind=kind, symbol=self.instrument(symbol).symbol if symbol else None)

    def note(self, text: str, symbol: Optional[str] = None, data: Optional[dict] = None) -> JournalEntry:
        return self.store.journal(JournalEntry(kind="note", symbol=self.instrument(symbol).symbol if symbol else None, text=text, data=data))

    def set_kill_switch(self, on: bool) -> bool:
        path = self.settings.resolve(self.settings.risk.kill_switch_file)
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"kill switch engaged {utcnow().isoformat()}\n")
            self.store.journal(JournalEntry(kind="system", text="kill switch ON"))
        elif path.exists():
            path.unlink()
            self.store.journal(JournalEntry(kind="system", text="kill switch OFF"))
        return self.risk.kill_switch_active()

    def doctor(self, include_data: bool = True) -> dict:
        checks: list[CheckResult] = []
        checks.append(CheckResult(name="config", ok=True, detail=f"{self.settings.config_path or 'defaults'}; db={self.settings.resolve(self.settings.db_path)}"))
        checks.append(CheckResult(name="kill_switch", ok=not self.risk.kill_switch_active(),
                                  detail="active" if self.risk.kill_switch_active() else "inactive"))
        checks.append(CheckResult(name="live_trading", ok=True, detail="ENABLED" if self.settings.live_trading_enabled else "disabled (paper only)"))
        for m in Market:
            sess = market_session(m)
            checks.append(CheckResult(name=f"session:{m.value}", ok=True, detail=("OPEN, " + sess["detail"]) if sess["open"] else sess["detail"]))
        if include_data:
            checks.extend(self.data.check_all())
        for name in self.brokers.names():
            b = self.brokers.get(name)
            if not b.available():
                checks.append(CheckResult(name=f"broker:{name}", ok=False, detail="credentials not configured (skipped)"))
            else:
                checks.append(b.check())
        return {"version": __version__, "checks": [c.model_dump() for c in checks],
                "ok": all(c.ok for c in checks if not c.detail.startswith("credentials not configured"))}
