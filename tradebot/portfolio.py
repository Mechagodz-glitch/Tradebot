"""Long-term portfolio sleeve: a target-weight model, cash-flow rebalancing and a dividend calendar.

The model lives in ``portfolio.yaml`` (sleeves -> holdings -> weights) and the payout data in
``data/dividends/in.yaml``. ``plan`` turns a monthly contribution into a buy list that fills the largest
gaps to target first (so rebalancing is done with new money, not sales), preferring names whose next
dividend record date is close. Portfolio holdings are not theses: no stop-loss, and symbols that currently
carry an open thesis are skipped so the two sleeves never fight over the same shares."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from .errors import TradebotError
from .models import Market, OrderRequest, OrderType, Side
from .ticks import round_to_tick

MODEL_FILE = "portfolio.yaml"
DIVIDENDS_FILE = "data/dividends/in.yaml"
RECORD_LEAD_MONTHS = 1          # a payout month M usually has its record date in month M-1
ENTRY_LIMIT_BPS = 15.0          # marketable limit: last * (1 + bps/1e4), tick aligned
PRIORITY_BOOST = 1.25           # deficit multiplier for names with a record date this month or next
LUMPY_FRACTION = 0.5            # buy one share of a high-priced name once the gap is at least this fraction of a share


class PortfolioError(TradebotError):
    category = "portfolio"


class Sleeve(BaseModel):
    weight: float = Field(ge=0)
    holdings: dict[str, float] = Field(default_factory=dict)


class PortfolioModel(BaseModel):
    market: Market = Market.IN
    venue: str = "kite"
    currency: str = "INR"
    cash_buffer: float = 0.0
    sleeves: dict[str, Sleeve] = Field(default_factory=dict)

    def targets(self) -> dict[str, float]:
        """Effective target weight per symbol (sleeve weight x normalised holding weight). Sums to 1."""
        # a sleeve with no live holdings (e.g. an unfunded satellite) lends its weight to the others
        active = [s for s in self.sleeves.values() if any(w > 0 for w in s.holdings.values())]
        total_sleeve = sum(s.weight for s in active) or 1.0
        out: dict[str, float] = {}
        for s in active:
            live = {k.upper(): w for k, w in s.holdings.items() if w > 0}
            tot = sum(live.values())
            for sym, w in live.items():
                out[sym] = out.get(sym, 0.0) + (s.weight / total_sleeve) * (w / tot)
        return out

    def sleeve_of(self, symbol: str) -> Optional[str]:
        for name, s in self.sleeves.items():
            if symbol.upper() in {k.upper() for k in s.holdings}:
                return name
        return None


def load_model(root: str, path: Optional[str] = None) -> PortfolioModel:
    p = Path(path or MODEL_FILE)
    if not p.is_absolute():
        p = Path(root) / p
    if not p.exists():
        raise PortfolioError(f"portfolio model not found: {p}", code="no_model")
    return PortfolioModel.model_validate(yaml.safe_load(p.read_text()) or {})


def load_dividends(root: str, path: Optional[str] = None) -> dict[str, dict]:
    p = Path(path or DIVIDENDS_FILE)
    if not p.is_absolute():
        p = Path(root) / p
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    return {k.upper(): (v or {}) for k, v in data.items()}


# ---- helpers ---------------------------------------------------------------------------------------
def _month_add(m: int, delta: int) -> int:
    return (m - 1 + delta) % 12 + 1


def record_months(div: dict) -> set[int]:
    """Months in which a name's record dates typically fall (payout month minus the lead)."""
    return {_month_add(int(pmt["month"]), -RECORD_LEAD_MONTHS) for pmt in div.get("payments", []) if pmt.get("month")}


def annual_dps(div: dict) -> float:
    if div.get("dps_fy26") is not None:
        return float(div["dps_fy26"])
    return float(sum(float(p.get("amount") or 0) for p in div.get("payments", [])))


def _holdings(engine, model: PortfolioModel) -> tuple[dict[str, dict], float]:
    """Current venue positions restricted to model symbols, and the venue cash."""
    targets = model.targets()
    positions = {p.symbol.upper(): p for p in engine.positions(venue=model.venue, market=model.market)}
    held: dict[str, dict] = {}
    for sym in targets:
        p = positions.get(sym)
        if p and p.qty > 0:
            price = p.market_price or p.avg_price
            held[sym] = {"qty": float(p.qty), "avg_price": float(p.avg_price), "price": float(price), "value": float(p.qty) * float(price)}
    try:
        cash = float(engine.account(model.market, model.venue).cash)
    except Exception:  # noqa: BLE001
        cash = 0.0
    return held, cash


def _prices(engine, model: PortfolioModel, symbols: list[str]) -> dict[str, float]:
    insts = [engine.instrument(s, model.market) for s in symbols]
    try:
        quotes = engine.data.quote_many(insts)
    except Exception:  # noqa: BLE001
        quotes = {}
    out: dict[str, float] = {}
    for s in symbols:
        q = quotes.get(s.upper())
        if q is None:
            try:
                q = engine.quote(s, model.market)
            except Exception:  # noqa: BLE001
                q = None
        if q is not None and q.last:
            out[s.upper()] = float(q.ask or q.last)
    return out


# ---- reports ---------------------------------------------------------------------------------------
def status(engine, model: PortfolioModel, dividends: Optional[dict] = None, today: Optional[date] = None) -> dict:
    dividends = dividends if dividends is not None else load_dividends(engine.settings.root)
    targets = model.targets()
    held, cash = _holdings(engine, model)
    total = sum(h["value"] for h in held.values())
    thesis_syms = engine.thesis_symbols(venue=model.venue)
    rows = []
    annual = 0.0
    by_month: dict[int, float] = {m: 0.0 for m in range(1, 13)}
    for sym, tw in sorted(targets.items(), key=lambda kv: -kv[1]):
        h = held.get(sym, {"qty": 0.0, "avg_price": None, "price": None, "value": 0.0})
        cw = (h["value"] / total) if total > 0 else 0.0
        div = dividends.get(sym, {})
        dps = annual_dps(div)
        income = h["qty"] * dps
        annual += income
        for pmt in div.get("payments", []):
            by_month[int(pmt["month"])] += h["qty"] * float(pmt.get("amount") or 0)
        rows.append({"symbol": sym, "sleeve": model.sleeve_of(sym), "target_w": tw, "current_w": cw, "drift_pp": (cw - tw) * 100,
                     "qty": h["qty"], "avg_price": h["avg_price"], "price": h["price"], "value": h["value"],
                     "dps": dps, "yield_pct": (dps / h["price"] * 100) if h["price"] else None, "annual_dividend": income,
                     "thesis_managed": sym in thesis_syms})
    return {"market": model.market.value, "venue": model.venue, "currency": model.currency, "portfolio_value": total, "cash": cash,
            "cash_buffer": model.cash_buffer, "rows": rows, "annual_dividend": annual,
            "dividends_by_month": {str(m): round(v, 2) for m, v in by_month.items()},
            "not_in_model": sorted(set(h.upper() for h in engine.positions(venue=model.venue, market=model.market) and
                                        [p.symbol for p in engine.positions(venue=model.venue, market=model.market) if p.qty > 0]) - set(targets))}


def plan(engine, model: PortfolioModel, contribution: float, dividends: Optional[dict] = None, today: Optional[date] = None,
         limit_bps: float = ENTRY_LIMIT_BPS) -> dict:
    """Cash-flow rebalancing: spend ``contribution`` (plus idle cash above the buffer) on the names furthest
    below target, whole shares, marketable tick-aligned limits. Never sells."""
    dividends = dividends if dividends is not None else load_dividends(engine.settings.root)
    today = today or date.today()
    targets = model.targets()
    if not targets:
        raise PortfolioError("portfolio model has no holdings with positive weight", code="empty_model")
    held, cash = _holdings(engine, model)
    thesis_syms = engine.thesis_symbols(venue=model.venue)
    prices = _prices(engine, model, list(targets))
    current_total = sum(h["value"] for h in held.values())
    budget = max(0.0, contribution + max(0.0, cash - model.cash_buffer))
    future_total = current_total + budget
    soon = {today.month, _month_add(today.month, 1)}

    deficits = []
    skipped = []
    for sym, tw in targets.items():
        if sym in thesis_syms:
            skipped.append({"symbol": sym, "why": "open thesis manages this symbol"})
            continue
        px = prices.get(sym)
        if not px:
            skipped.append({"symbol": sym, "why": "no price"})
            continue
        target_value = tw * future_total
        gap = target_value - held.get(sym, {"value": 0.0})["value"]
        if gap <= 0:
            continue
        boost = PRIORITY_BOOST if record_months(dividends.get(sym, {})) & soon else 1.0
        deficits.append({"symbol": sym, "gap": gap, "score": gap * boost, "price": px, "target_value": target_value,
                         "record_soon": boost > 1.0})
    deficits.sort(key=lambda d: -d["score"])

    remaining = budget
    buys = []
    max_order = engine.settings.risk.max_order_notional.get(model.currency)
    max_pos = engine.settings.risk.max_position_notional.get(model.currency)
    for d in deficits:                                  # first pass: fill gaps in priority order
        if remaining < d["price"]:
            continue
        qty = int(min(d["gap"], remaining) // d["price"])
        if qty <= 0 and d["gap"] >= LUMPY_FRACTION * d["price"]:
            qty = 1                                     # high-priced share: one lot when the gap is at least half a share
        if qty <= 0:
            continue
        limit = round_to_tick(d["price"] * (1 + limit_bps / 1e4), model.market, Side.BUY)
        notional = qty * limit
        flags = []
        if max_order and notional > max_order:
            flags.append(f"exceeds risk.max_order_notional {max_order:,.0f}")
        if max_pos and held.get(d["symbol"], {"value": 0.0})["value"] + notional > max_pos:
            flags.append(f"position would exceed risk.max_position_notional {max_pos:,.0f}")
        buys.append({"symbol": d["symbol"], "sleeve": model.sleeve_of(d["symbol"]), "qty": qty, "limit": limit, "notional": notional,
                     "gap_before": d["gap"], "record_soon": d["record_soon"], "risk_flags": flags})
        remaining -= notional
    # second pass: leftover cash buys one more share of the largest remaining gap while affordable
    changed = True
    while changed and remaining > 0:
        changed = False
        for d in deficits:
            b = next((x for x in buys if x["symbol"] == d["symbol"]), None)
            bought = b["notional"] if b else 0.0
            if d["gap"] - bought >= LUMPY_FRACTION * d["price"] and remaining >= d["price"]:
                limit = round_to_tick(d["price"] * (1 + limit_bps / 1e4), model.market, Side.BUY)
                if b:
                    b["qty"] += 1; b["notional"] += limit
                else:
                    buys.append({"symbol": d["symbol"], "sleeve": model.sleeve_of(d["symbol"]), "qty": 1, "limit": limit, "notional": limit,
                                 "gap_before": d["gap"], "record_soon": d["record_soon"], "risk_flags": []})
                remaining -= limit
                changed = True
                break
    return {"market": model.market.value, "venue": model.venue, "currency": model.currency, "as_of": today.isoformat(),
            "contribution": contribution, "idle_cash": cash, "cash_buffer": model.cash_buffer, "budget": budget,
            "portfolio_value_before": current_total, "buys": buys, "spend": sum(b["notional"] for b in buys),
            "unspent": remaining, "skipped": skipped,
            "deficits": [{k: v for k, v in d.items() if k != "score"} for d in deficits]}


def execute_plan(engine, model: PortfolioModel, planned: dict, dry_run: bool = False) -> list[dict]:
    """Send the plan's buys as limit orders through the risk engine (one order per symbol)."""
    out = []
    for b in planned["buys"]:
        req = OrderRequest(symbol=b["symbol"], side=Side.BUY, qty=b["qty"], order_type=OrderType.LIMIT, limit_price=b["limit"],
                           venue=model.venue, market=model.market, reason=f"portfolio plan {planned['as_of']} ({b['sleeve']})",
                           strategy="portfolio")
        try:
            o = engine.place_order(req, dry_run=dry_run)
            out.append({"symbol": b["symbol"], "qty": b["qty"], "limit": b["limit"], "status": o.status.value, "order_id": o.id})
        except TradebotError as e:
            out.append({"symbol": b["symbol"], "qty": b["qty"], "limit": b["limit"], "status": "rejected", "error": e.message, "code": e.code})
    return out


def calendar(engine, model: PortfolioModel, dividends: Optional[dict] = None, months: int = 3, today: Optional[date] = None) -> dict:
    """Upcoming payouts for held names over the next ``months`` months: expected credits and record windows."""
    dividends = dividends if dividends is not None else load_dividends(engine.settings.root)
    today = today or date.today()
    held, _cash = _holdings(engine, model)
    window = [_month_add(today.month, i) for i in range(months + 1)]
    events = []
    for sym in model.targets():
        div = dividends.get(sym, {})
        qty = held.get(sym, {"qty": 0.0})["qty"]
        for pmt in div.get("payments", []):
            m = int(pmt["month"])
            if m in window:
                events.append({"symbol": sym, "pay_month": m, "record_month": _month_add(m, -RECORD_LEAD_MONTHS), "dps": float(pmt.get("amount") or 0),
                               "qty_held": qty, "expected": qty * float(pmt.get("amount") or 0), "note": pmt.get("note"),
                               "buy_before": "record date this month" if _month_add(m, -RECORD_LEAD_MONTHS) == today.month else None})
    events.sort(key=lambda e: (window.index(e["pay_month"]), -e["expected"]))
    total = sum(e["expected"] for e in events)
    by_month = {}
    for e in events:
        by_month[str(e["pay_month"])] = round(by_month.get(str(e["pay_month"]), 0.0) + e["expected"], 2)
    return {"as_of": today.isoformat(), "months": months, "events": events, "expected_total": total, "by_month": by_month,
            "reinvest_note": "Dividends are paid to your bank account, not to Zerodha: add the month's expected total to the next contribution."}
