"""Rule-based strategies that turn candles + current positions into an executable plan.

The plan is deterministic and fully explained: every proposed order carries the rule that produced
it, and that text becomes the order's journal ``reason``. The intelligence layer decides whether to
run a plan; the strategy never sends orders on its own."""

from __future__ import annotations

import math
from statistics import mean
from typing import Optional

from pydantic import BaseModel, Field

from .config import StrategyConfig
from .errors import TradebotError
from .models import Market, Order, OrderRequest, OrderType, Side, TimeInForce, utcnow


class SignalRow(BaseModel):
    symbol: str
    last: float
    price_source: str
    sma_fast: Optional[float] = None
    sma_slow: Optional[float] = None
    momentum: Optional[float] = None
    uptrend: bool = False
    held_qty: float = 0.0
    avg_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    note: str = ""


class PlanItem(BaseModel):
    symbol: str
    side: Side
    qty: float
    order_type: OrderType
    limit_price: Optional[float] = None
    notional: float
    reason: str


class Plan(BaseModel):
    strategy: str
    venue: str
    market: Market
    ts: str = Field(default_factory=lambda: utcnow().isoformat())
    equity: float
    cash: float
    signals: list[SignalRow]
    orders: list[PlanItem]
    notes: list[str] = Field(default_factory=list)


class TrendStrategy:
    """Long-only daily trend following.

    Entry: close > SMA(fast) > SMA(slow) and N-day momentum > 0; rank by momentum, take the top
    ``max_positions``. Size each position at ``position_fraction`` of equity (whole units for stocks).
    Exit: close < SMA(fast) (trend break) or price < avg entry * (1 - stop_loss_pct).
    Holding a name that is still in an uptrend but has slipped in the ranking is fine (no churn)."""

    name = "trend"

    def __init__(self, engine, cfg: Optional[StrategyConfig] = None):
        self.engine = engine
        self.cfg = cfg or engine.settings.strategy

    # ---- analysis -------------------------------------------------------------
    def _row(self, symbol: str, market: Market, positions: dict) -> SignalRow:
        cfg = self.cfg
        need = max(cfg.slow_sma, cfg.momentum_days + 1, cfg.min_history) + 5
        candles, src = self.engine.candles(symbol, "1d", limit=need, market=market)
        closes = [c.close for c in candles]
        pos = positions.get(symbol)
        held = pos.qty if pos else 0.0
        avg = pos.avg_price if pos and pos.avg_price else None
        # current price: live quote if we can get one, else the last close
        last, psrc = (closes[-1], f"close:{src}") if closes else (None, "none")
        try:
            q = self.engine.quote(symbol, market)
            last, psrc = q.last, f"quote:{q.source}"
        except TradebotError:
            pass
        if last is None:
            return SignalRow(symbol=symbol, last=0.0, price_source=psrc, held_qty=held, avg_price=avg, note="no price")
        if len(closes) < cfg.min_history:
            return SignalRow(symbol=symbol, last=last, price_source=psrc, held_qty=held, avg_price=avg,
                             note=f"insufficient history ({len(closes)} < {cfg.min_history})")
        sma_f = mean(closes[-cfg.fast_sma:])
        sma_s = mean(closes[-cfg.slow_sma:])
        mom = closes[-1] / closes[-1 - cfg.momentum_days] - 1.0
        up = last > sma_f > sma_s and mom > 0  # live price against the moving averages
        pnl = (last / avg - 1.0) * 100 if avg else None
        return SignalRow(symbol=symbol, last=last, price_source=psrc, sma_fast=sma_f, sma_slow=sma_s, momentum=mom,
                         uptrend=up, held_qty=held, avg_price=avg, pnl_pct=pnl)

    @staticmethod
    def _round_qty(qty: float, market: Market) -> float:
        if market == Market.CRYPTO:
            return math.floor(qty * 1e6) / 1e6
        return float(math.floor(qty))

    def plan(self, market: Market, venue: Optional[str] = None) -> Plan:
        cfg = self.cfg
        broker = self.engine.venue(venue)
        universe = cfg.universe.get(market.value, [])
        if not universe:
            raise TradebotError(f"strategy universe for market {market.value} is empty", code="config_error")
        acct = broker.account(market)
        positions = {p.symbol: p for p in broker.positions(market) if abs(p.qty) > 1e-12}
        rows = [self._row(s, market, positions) for s in universe]
        by_sym = {r.symbol: r for r in rows}
        notes: list[str] = []
        orders: list[PlanItem] = []

        # ---- exits
        exiting: set[str] = set()
        for sym, pos in positions.items():
            r = by_sym.get(sym)
            if r is None:
                notes.append(f"{sym}: held but not in the strategy universe; left untouched")
                continue
            reasons = []
            if r.sma_fast is not None and r.last < r.sma_fast:
                reasons.append(f"trend break: {r.last:.2f} < SMA{cfg.fast_sma} {r.sma_fast:.2f}")
            if r.avg_price and r.last < r.avg_price * (1 - cfg.stop_loss_pct / 100):
                reasons.append(f"stop loss: {r.pnl_pct:.2f}% <= -{cfg.stop_loss_pct}%")
            if reasons:
                exiting.add(sym)
                orders.append(PlanItem(symbol=sym, side=Side.SELL, qty=abs(pos.qty), order_type=OrderType(cfg.exit_order_type),
                                       notional=abs(pos.qty) * r.last, reason="[trend] exit: " + "; ".join(reasons)))

        # ---- entries
        ranked = sorted([r for r in rows if r.uptrend], key=lambda r: -(r.momentum or 0))
        targets = [r.symbol for r in ranked[: cfg.max_positions]]
        held_after = [s for s in positions if s not in exiting]
        slots = cfg.max_positions - len(held_after)
        deployable = acct.cash + sum(o.notional for o in orders if o.side == Side.SELL) - acct.equity * cfg.cash_buffer_fraction
        for sym in targets:
            if sym in held_after or sym in exiting:
                continue  # never re-enter a name being exited in the same cycle
            if slots <= 0:
                notes.append(f"{sym}: in uptrend but no free slot (max_positions={cfg.max_positions})")
                continue
            r = by_sym[sym]
            budget = min(acct.equity * cfg.position_fraction, deployable)
            max_order = self.engine.settings.risk.max_order_notional.get(r and self.engine.instrument(sym, market).currency)
            if max_order:
                budget = min(budget, max_order * 0.98)
            limit = r.last * (1 + cfg.entry_limit_offset_bps / 10_000)
            qty = self._round_qty(budget / limit, market)
            if qty <= 0:
                notes.append(f"{sym}: budget {budget:.2f} buys less than one unit at {r.last:.2f}")
                continue
            notional = qty * limit
            orders.append(PlanItem(symbol=sym, side=Side.BUY, qty=qty, order_type=OrderType.LIMIT, limit_price=round(limit, 2),
                                   notional=notional,
                                   reason=f"[trend] entry: close > SMA{cfg.fast_sma} > SMA{cfg.slow_sma}, "
                                          f"{cfg.momentum_days}d momentum {r.momentum * 100:+.2f}% (rank {targets.index(sym) + 1})"))
            deployable -= notional
            slots -= 1
        if not ranked:
            notes.append("no symbol in the universe is in an uptrend; nothing to buy")
        return Plan(strategy=self.name, venue=broker.name, market=market, equity=acct.equity, cash=acct.cash,
                    signals=rows, orders=orders, notes=notes)

    # ---- execution ----------------------------------------------------------------
    def execute(self, plan: Plan, dry_run: bool = False) -> list[dict]:
        results = []
        # sells first so their proceeds are available to the buys
        for item in sorted(plan.orders, key=lambda o: 0 if o.side == Side.SELL else 1):
            tif = TimeInForce.GTC if plan.market == Market.CRYPTO else TimeInForce.DAY
            req = OrderRequest(symbol=item.symbol, side=item.side, qty=item.qty, order_type=item.order_type,
                               limit_price=item.limit_price, tif=tif, venue=plan.venue, market=plan.market,
                               reason=item.reason, strategy=self.name)
            try:
                order: Order = self.engine.place_order(req, dry_run=dry_run)
                results.append({"symbol": item.symbol, "side": item.side.value, "qty": item.qty, "status": order.status.value,
                                "order_id": order.id, "avg_fill_price": order.avg_fill_price, "detail": order.reject_reason})
            except TradebotError as e:
                results.append({"symbol": item.symbol, "side": item.side.value, "qty": item.qty, "status": "rejected",
                                "order_id": None, "avg_fill_price": None, "detail": f"{e.code}: {e.message}"})
        return results


STRATEGIES = {"trend": TrendStrategy}


def get_strategy(engine, name: Optional[str] = None):
    name = name or engine.settings.strategy.name
    if name not in STRATEGIES:
        raise TradebotError(f"unknown strategy {name!r}; known: {sorted(STRATEGIES)}", code="config_error")
    return STRATEGIES[name](engine)
