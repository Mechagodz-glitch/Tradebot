"""Command line interface. Every command accepts ``--json`` for machine readable output."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .errors import TradebotError
from .models import Market, OrderRequest, OrderType, Side, TimeInForce

app = typer.Typer(help="Tradebot: deterministic paper/live execution layer for US, Indian and crypto markets.",
                  no_args_is_help=True, add_completion=False)
console = Console()
err_console = Console(stderr=True)

_state: dict = {"json": False, "config": None}


def _engine():
    from .config import load_settings
    from .engine import TradingEngine
    return TradingEngine(load_settings(_state["config"]))


def _dump(obj) -> str:
    def default(o):
        if hasattr(o, "model_dump"):
            return o.model_dump(mode="json")
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, indent=2, default=default)


def _out(data, table_fn=None):
    if _state["json"] or table_fn is None:
        console.print_json(_dump(data)) if sys.stdout.isatty() else print(_dump(data))
    else:
        table_fn(data)


def _fmt(v, nd=2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{nd}f}" if abs(v) >= 1 else f"{v:.6g}"
    return str(v)


def _handle(fn):
    try:
        return fn()
    except TradebotError as e:
        if _state["json"]:
            print(_dump(e.to_dict()))
        else:
            err_console.print(f"[red]{e.category}[/red] ({e.code}): {e.message}" + (f"  {e.details}" if e.details else ""))
        raise typer.Exit(2)
    except (ValueError, FileNotFoundError) as e:
        if _state["json"]:
            print(_dump({"error": "invalid", "message": str(e)}))
        else:
            err_console.print(f"[red]invalid[/red]: {e}")
        raise typer.Exit(2)


@app.callback()
def main(json_: bool = typer.Option(False, "--json", help="Machine readable JSON output"),
         config: Optional[str] = typer.Option(None, "--config", help="Path to config.yaml")):
    _state["json"] = json_
    _state["config"] = config


# ---------------------------------------------------------------- diagnostics
@app.command()
def doctor(no_data: bool = typer.Option(False, "--no-data", help="Skip data provider checks")):
    """Check config, connectivity to data providers and configured venues."""
    def run():
        eng = _engine()
        res = eng.doctor(include_data=not no_data)
        def table(r):
            t = Table(title=f"tradebot {r['version']} doctor")
            t.add_column("check"); t.add_column("ok"); t.add_column("detail"); t.add_column("ms", justify="right")
            for c in r["checks"]:
                t.add_row(c["name"], "[green]yes[/green]" if c["ok"] else "[red]no[/red]", c["detail"], str(c.get("latency_ms") or ""))
            console.print(t)
        _out(res, table)
    _handle(run)


@app.command()
def venues():
    """List execution venues and whether they are configured."""
    def run():
        eng = _engine()
        rows = []
        for n in eng.brokers.names():
            b = eng.brokers.get(n)
            rows.append({"venue": n, "configured": b.available(), "live": b.live, "markets": [m.value for m in b.markets],
                         "default": n == eng.settings.default_venue})
        def table(rows):
            t = Table(title="venues")
            for c in ("venue", "configured", "live", "markets", "default"):
                t.add_column(c)
            for r in rows:
                t.add_row(r["venue"], str(r["configured"]), str(r["live"]), ",".join(r["markets"]), "*" if r["default"] else "")
            console.print(t)
        _out(rows, table)
    _handle(run)


# ---------------------------------------------------------------- data
@app.command()
def quote(symbols: list[str] = typer.Argument(..., help="e.g. BTC-USD AAPL NSE:RELIANCE"),
          market: Optional[Market] = typer.Option(None, help="Force market: us, in, crypto"),
          provider: Optional[str] = typer.Option(None, help="Force a data provider")):
    """Latest quote for one or more symbols."""
    def run():
        eng = _engine()
        rows = []
        for s in symbols:
            try:
                rows.append(eng.quote(s, market, provider).model_dump(mode="json"))
            except TradebotError as e:
                rows.append({"symbol": s.upper(), "error": e.message})
        def table(rows):
            t = Table(title="quotes")
            for c in ("symbol", "last", "bid", "ask", "prev_close", "chg%", "ccy", "source", "ts"):
                t.add_column(c)
            for r in rows:
                if "error" in r:
                    t.add_row(r["symbol"], f"[red]{r['error']}[/red]", "", "", "", "", "", "", "")
                    continue
                chg = ((r["last"] / r["prev_close"]) - 1) * 100 if r.get("prev_close") else None
                t.add_row(r["symbol"], _fmt(r["last"]), _fmt(r.get("bid")), _fmt(r.get("ask")), _fmt(r.get("prev_close")),
                          f"{chg:+.2f}" if chg is not None else "-", r["currency"], r["source"], r["ts"][:19])
            console.print(t)
        _out(rows, table)
    _handle(run)


@app.command()
def candles(symbol: str, interval: str = typer.Option("1d", help="1m 5m 15m 30m 1h 4h 1d 1w"),
            limit: int = typer.Option(30), market: Optional[Market] = typer.Option(None),
            provider: Optional[str] = typer.Option(None)):
    """Historical OHLCV candles."""
    def run():
        eng = _engine()
        rows, src = eng.candles(symbol, interval, limit, market, provider=provider)
        data = {"symbol": eng.instrument(symbol, market).symbol, "interval": interval, "source": src,
                "candles": [c.model_dump(mode="json") for c in rows]}
        def table(d):
            t = Table(title=f"{d['symbol']} {d['interval']} ({d['source']})")
            for c in ("ts", "open", "high", "low", "close", "volume"):
                t.add_column(c, justify="right")
            for c in d["candles"]:
                t.add_row(c["ts"][:16], _fmt(c["open"]), _fmt(c["high"]), _fmt(c["low"]), _fmt(c["close"]), _fmt(c["volume"], 0))
            console.print(t)
        _out(data, table)
    _handle(run)


@app.command()
def search(text: str, market: Market = typer.Option(Market.IN)):
    """Search instruments (Indian market via Upstox master)."""
    _handle(lambda: _out(_engine().search(text, market)))


# ---------------------------------------------------------------- account
@app.command()
def account(venue: Optional[str] = typer.Option(None), market: Optional[Market] = typer.Option(None)):
    """Cash, equity and P&L per market on a venue."""
    def run():
        eng = _engine()
        rows = eng.pnl(venue, market)
        def table(rows):
            t = Table(title=f"account ({rows[0]['venue'] if rows else ''})")
            for c in ("market", "ccy", "cash", "positions", "equity", "realized", "unrealized", "total pnl", "day pnl", "open pos"):
                t.add_column(c, justify="right")
            for r in rows:
                if "error" in r:
                    t.add_row(r["market"], f"[red]{r['error'][:60]}[/red]", "", "", "", "", "", "", "", "")
                    continue
                t.add_row(r["market"], r["currency"], _fmt(r["cash"]), _fmt(r["positions_value"]), _fmt(r["equity"]),
                          _fmt(r["realized_pnl"]), _fmt(r["unrealized_pnl"]), _fmt(r["total_pnl"]), _fmt(r["day_pnl"]), str(r["open_positions"]))
            console.print(t)
        _out(rows, table)
    _handle(run)


@app.command()
def positions(venue: Optional[str] = typer.Option(None), market: Optional[Market] = typer.Option(None)):
    """Open positions with marks and unrealized P&L."""
    def run():
        eng = _engine()
        rows = [p.model_dump(mode="json") for p in eng.positions(venue, market)]
        def table(rows):
            t = Table(title="positions")
            for c in ("venue", "symbol", "qty", "avg", "mark", "value", "unrealized", "realized", "ccy"):
                t.add_column(c, justify="right")
            for r in rows:
                t.add_row(r["venue"], r["symbol"], _fmt(r["qty"], 6), _fmt(r["avg_price"]), _fmt(r["market_price"]),
                          _fmt(r["market_value"]), _fmt(r["unrealized_pnl"]), _fmt(r["realized_pnl"]), r["currency"])
            console.print(t)
            if not rows:
                console.print("(no open positions)")
        _out(rows, table)
    _handle(run)


@app.command()
def orders(venue: Optional[str] = typer.Option(None), symbol: Optional[str] = typer.Option(None),
           open_only: bool = typer.Option(False, "--open"), limit: int = typer.Option(30),
           refresh: bool = typer.Option(False, help="Refresh open orders from the venue")):
    """Order history (most recent first)."""
    def run():
        eng = _engine()
        rows = [o.model_dump(mode="json") for o in eng.orders(venue, symbol, open_only, limit, refresh=refresh)]
        _out(rows, _orders_table)
    _handle(run)


def _orders_table(rows):
    t = Table(title="orders")
    for c in ("id", "venue", "symbol", "side", "qty", "type", "limit", "stop", "status", "filled", "avg fill", "created", "reason"):
        t.add_column(c)
    for r in rows:
        status = r["status"]
        color = {"filled": "green", "rejected": "red", "canceled": "yellow", "expired": "yellow"}.get(status, "cyan")
        t.add_row(r["id"], r["venue"], r["symbol"], r["side"], _fmt(r["qty"], 6), r["order_type"], _fmt(r["limit_price"]),
                  _fmt(r["stop_price"]), f"[{color}]{status}[/{color}]", _fmt(r["filled_qty"], 6), _fmt(r["avg_fill_price"]),
                  r["created_at"][:19], escape((r.get("reject_reason") or r.get("reason") or "")[:60]))
    console.print(t)
    if not rows:
        console.print("(no orders)")


@app.command()
def order(order_id: str):
    """Show one order (accepts tradebot id, venue id or client id)."""
    _handle(lambda: _out(_engine().order(order_id).model_dump(mode="json"), lambda o: _orders_table([o])))


# ---------------------------------------------------------------- trading
def _place(side: Side, symbol: str, qty: float, order_type: OrderType, limit: Optional[float], stop: Optional[float],
           tif: TimeInForce, venue: Optional[str], market: Optional[Market], reason: Optional[str], strategy: Optional[str],
           client_id: Optional[str], dry_run: bool):
    def run():
        eng = _engine()
        req = OrderRequest(symbol=symbol, side=side, qty=qty, order_type=order_type, limit_price=limit, stop_price=stop,
                           tif=tif, venue=venue or eng.settings.default_venue, market=market, reason=reason,
                           strategy=strategy, client_order_id=client_id)
        o = eng.place_order(req, dry_run=dry_run)
        _out(o.model_dump(mode="json"), lambda d: _orders_table([d]))
        if o.status.value == "rejected" and not _state["json"]:
            raise typer.Exit(3)
    _handle(run)


_ORDER_OPTS = dict(
    order_type=typer.Option(OrderType.MARKET, "--type"), limit=typer.Option(None, "--limit", help="Limit price"),
    stop=typer.Option(None, "--stop", help="Stop / trigger price"), tif=typer.Option(TimeInForce.DAY, "--tif"),
    venue=typer.Option(None, help="paper | alpaca | kite | ccxt (default from config)"),
    market=typer.Option(None, help="us | in | crypto (auto-detected from symbol)"),
    reason=typer.Option(None, help="Why this trade is being made (stored in the journal)"),
    strategy=typer.Option(None, help="Strategy tag"), client_id=typer.Option(None, "--client-id"),
    dry_run=typer.Option(False, "--dry-run", help="Run risk checks only, do not send"),
)


@app.command()
def buy(symbol: str, qty: float, order_type: OrderType = _ORDER_OPTS["order_type"], limit: Optional[float] = _ORDER_OPTS["limit"],
        stop: Optional[float] = _ORDER_OPTS["stop"], tif: TimeInForce = _ORDER_OPTS["tif"], venue: Optional[str] = _ORDER_OPTS["venue"],
        market: Optional[Market] = _ORDER_OPTS["market"], reason: Optional[str] = _ORDER_OPTS["reason"],
        strategy: Optional[str] = _ORDER_OPTS["strategy"], client_id: Optional[str] = _ORDER_OPTS["client_id"],
        dry_run: bool = _ORDER_OPTS["dry_run"]):
    """Place a buy order."""
    _place(Side.BUY, symbol, qty, order_type, limit, stop, tif, venue, market, reason, strategy, client_id, dry_run)


@app.command()
def sell(symbol: str, qty: float, order_type: OrderType = _ORDER_OPTS["order_type"], limit: Optional[float] = _ORDER_OPTS["limit"],
         stop: Optional[float] = _ORDER_OPTS["stop"], tif: TimeInForce = _ORDER_OPTS["tif"], venue: Optional[str] = _ORDER_OPTS["venue"],
         market: Optional[Market] = _ORDER_OPTS["market"], reason: Optional[str] = _ORDER_OPTS["reason"],
         strategy: Optional[str] = _ORDER_OPTS["strategy"], client_id: Optional[str] = _ORDER_OPTS["client_id"],
         dry_run: bool = _ORDER_OPTS["dry_run"]):
    """Place a sell order."""
    _place(Side.SELL, symbol, qty, order_type, limit, stop, tif, venue, market, reason, strategy, client_id, dry_run)


@app.command()
def cancel(order_id: str = typer.Argument(None), all_: bool = typer.Option(False, "--all", help="Cancel all open orders"),
           venue: Optional[str] = typer.Option(None), symbol: Optional[str] = typer.Option(None)):
    """Cancel one order, or all open orders."""
    def run():
        eng = _engine()
        if all_:
            _out([o.model_dump(mode="json") for o in eng.cancel_all(venue, symbol)], _orders_table)
        elif order_id:
            _out(eng.cancel_order(order_id).model_dump(mode="json"), lambda d: _orders_table([d]))
        else:
            raise ValueError("give an order id or --all")
    _handle(run)


@app.command()
def close(symbol: str, venue: Optional[str] = typer.Option(None), market: Optional[Market] = typer.Option(None),
          reason: Optional[str] = typer.Option(None)):
    """Close the whole position in a symbol with a market order."""
    _handle(lambda: _out(_engine().close_position(symbol, venue, market, reason).model_dump(mode="json"), lambda d: _orders_table([d])))


@app.command()
def sync(venue: Optional[str] = typer.Option(None)):
    """Process resting paper orders against live quotes, refresh live orders, snapshot equity."""
    _handle(lambda: _out(_engine().sync(venue)))


# ---------------------------------------------------------------- journal / control
@app.command()
def journal(limit: int = typer.Option(30), kind: Optional[str] = typer.Option(None, help="order fill note risk system"),
            symbol: Optional[str] = typer.Option(None)):
    """Trade journal: orders, fills, risk rejections and free-text notes."""
    def run():
        rows = [j.model_dump(mode="json") for j in _engine().journal(limit, kind, symbol)]
        def table(rows):
            t = Table(title="journal")
            for c in ("ts", "kind", "venue", "symbol", "order", "text"):
                t.add_column(c)
            for r in reversed(rows):
                t.add_row(r["ts"][:19], r["kind"], r.get("venue") or "", r.get("symbol") or "", r.get("order_id") or "", escape(r["text"]))
            console.print(t)
        _out(rows, table)
    _handle(run)


@app.command()
def note(text: str, symbol: Optional[str] = typer.Option(None), kind: str = typer.Option("note", help="note | thesis"),
         data: Optional[str] = typer.Option(None, help="JSON object with structured fields (direction, confidence, size, stop, target...)")):
    """Add a note or a structured thesis to the journal."""
    def run():
        payload = json.loads(data) if data else None
        _out(_engine().note(text, symbol, payload, kind=kind).model_dump(mode="json"))
    _handle(run)


@app.command()
def kill(off: bool = typer.Option(False, "--off", help="Disengage the kill switch")):
    """Engage the kill switch: every new order is rejected until --off."""
    _handle(lambda: _out({"kill_switch_active": _engine().set_kill_switch(not off)}))


@app.command("reset-paper")
def reset_paper(market: Optional[Market] = typer.Option(None), yes: bool = typer.Option(False, "--yes")):
    """Wipe paper orders, fills, positions and cash back to starting balances."""
    def run():
        if not yes:
            raise ValueError("refusing to reset without --yes")
        eng = _engine()
        eng.brokers.paper.reset(market)
        _out({"reset": market.value if market else "all"})
    _handle(run)


def _upsert_env(path, key: str, value: str) -> None:
    from .envfile import upsert_env
    upsert_env(path, key, value)


@app.command("kite-login")
def kite_login(request_token: Optional[str] = typer.Argument(None),
               save: bool = typer.Option(False, "--save", help="Write KITE_ACCESS_TOKEN into .env instead of printing it"),
               drop: bool = typer.Option(False, "--drop", help="With --save: also write the encrypted hand-off file for the other machines")):
    """Zerodha login: print the login URL, then exchange the request_token for an access token."""
    def run():
        eng = _engine()
        kite = eng.brokers.get("kite")
        if not request_token:
            _out({"login_url": kite.login_url(),
                  "next": "log in, copy request_token from the redirect URL, run: tradebot kite-login <request_token> --save"})
            return
        tok = kite.exchange_token(request_token)
        if save:
            from pathlib import Path
            env_path = Path(eng.settings.root) / ".env"
            _upsert_env(env_path, "KITE_ACCESS_TOKEN", tok)
            res = {"access_token": "<saved>", "saved_to": str(env_path),
                   "next": "run: tradebot doctor --no-data   (token is valid until about 6am IST tomorrow)"}
            if drop:
                from .tokendrop import write_drop
                res["drop"] = write_drop(eng.settings.root, tok)
                res["next"] = "git add data/secrets && git commit -m 'token drop' && git push   (the other machines apply it within minutes)"
            _out(res)
        else:
            _out({"access_token": tok, "next": "export KITE_ACCESS_TOKEN=<token> (valid until ~6am IST next day)"})
    _handle(run)


token_app = typer.Typer(help="Encrypted hand-off of the daily Kite token through the repo (see docs/VPS.md).", no_args_is_help=True)
app.add_typer(token_app, name="token")


@token_app.command("drop")
def token_drop():
    """Encrypt KITE_ACCESS_TOKEN from .env to every key in deploy/keys/*.pub and write data/secrets/kite_token.age."""
    def run():
        from .tokendrop import write_drop
        eng = _engine()
        res = write_drop(eng.settings.root)
        res["next"] = "git add data/secrets && git commit -m 'token drop' && git push"
        _out(res)
    _handle(run)


@token_app.command("apply")
def token_apply(identity: str = typer.Option("~/.ssh/id_ed25519", "--identity", help="SSH private key that matches one of deploy/keys/*.pub")):
    """Decrypt data/secrets/kite_token.age with your SSH key and write the token into .env (no-op if unchanged)."""
    def run():
        from .tokendrop import apply_drop
        eng = _engine()
        _out(apply_drop(eng.settings.root, identity))
    _handle(run)


strategy_app = typer.Typer(help="Rule-based strategy: show the plan, or run it.", no_args_is_help=True)
app.add_typer(strategy_app, name="strategy")


def _plan_tables(plan: dict):
    t = Table(title=f"{plan['strategy']} signals | {plan['venue']} {plan['market']} | equity {_fmt(plan['equity'])} cash {_fmt(plan['cash'])}")
    for c in ("symbol", "last", "src", "sma_fast", "sma_slow", "mom%", "uptrend", "eligible", "turnover", "held", "avg", "pnl%", "note"):
        t.add_column(c, justify="right")
    shown = [r for r in plan["signals"] if r["uptrend"] or r["held_qty"]] if len(plan["signals"]) > 40 else plan["signals"]
    shown = sorted(shown, key=lambda r: -(r["momentum"] or 0))[:40]
    for r in shown:
        t.add_row(r["symbol"], _fmt(r["last"]), r["price_source"], _fmt(r["sma_fast"]), _fmt(r["sma_slow"]),
                  f"{r['momentum'] * 100:+.2f}" if r["momentum"] is not None else "-",
                  "[green]yes[/green]" if r["uptrend"] else "no", "[green]yes[/green]" if r.get("eligible") else "no",
                  _fmt(r.get("turnover_cr"), 0), _fmt(r["held_qty"], 6), _fmt(r["avg_price"]),
                  f"{r['pnl_pct']:+.2f}" if r["pnl_pct"] is not None else "-", escape(r["note"]))
    console.print(t)
    if len(plan["signals"]) > len(shown):
        console.print(f"(showing {len(shown)} of {len(plan['signals'])} scanned; uptrend or held names only, by momentum)")
    o = Table(title="proposed orders")
    for c in ("symbol", "side", "qty", "type", "limit", "notional", "reason"):
        o.add_column(c)
    for it in plan["orders"]:
        o.add_row(it["symbol"], it["side"], _fmt(it["qty"], 6), it["order_type"], _fmt(it["limit_price"]), _fmt(it["notional"]), escape(it["reason"]))
    console.print(o)
    if not plan["orders"]:
        console.print("(no orders proposed)")
    for n in plan["notes"]:
        console.print(f"[yellow]note[/yellow] {n}")


@strategy_app.command("plan")
def strategy_plan(market: Market = typer.Option(Market.IN), venue: Optional[str] = typer.Option(None),
                  name: Optional[str] = typer.Option(None, "--name", help="strategy name (default from config)")):
    """Compute signals and the orders the strategy would place now. Sends nothing."""
    def run():
        from .strategy import get_strategy
        eng = _engine()
        plan = get_strategy(eng, name).plan(market, venue)
        _out(plan.model_dump(mode="json"), _plan_tables)
    _handle(run)


@strategy_app.command("run")
def strategy_run(market: Market = typer.Option(Market.IN), venue: Optional[str] = typer.Option(None),
                 name: Optional[str] = typer.Option(None, "--name"),
                 execute: bool = typer.Option(False, "--execute", help="Actually place the orders (default: risk-check only)")):
    """Compute the plan and place its orders (risk-checked dry run unless --execute)."""
    def run():
        from .strategy import get_strategy
        eng = _engine()
        strat = get_strategy(eng, name)
        plan = strat.plan(market, venue)
        results = strat.execute(plan, dry_run=not execute)
        eng.note(f"[{strat.name}] {'executed' if execute else 'dry run'} on {plan.venue}/{plan.market.value}: "
                 f"{len(plan.orders)} orders, {sum(1 for r in results if r['status'] in ('filled', 'accepted', 'new'))} accepted",
                 data={"results": results, "notes": plan.notes})
        out = {"plan": plan.model_dump(mode="json"), "executed": execute, "results": results}
        def table(d):
            _plan_tables(d["plan"])
            t = Table(title="execution results" + ("" if d["executed"] else " (dry run)"))
            for c in ("symbol", "side", "qty", "status", "order", "avg fill", "detail"):
                t.add_column(c)
            for r in d["results"]:
                color = {"filled": "green", "accepted": "cyan", "new": "cyan", "rejected": "red"}.get(r["status"], "white")
                t.add_row(r["symbol"], r["side"], _fmt(r["qty"], 6), f"[{color}]{r['status']}[/{color}]", r["order_id"] or "",
                          _fmt(r["avg_fill_price"]), escape((r["detail"] or "")[:90]))
            console.print(t)
        _out(out, table)
    _handle(run)


thesis_app = typer.Typer(help="Discretionary theses: positions with an enforced stop, target and expiry.", no_args_is_help=True)
app.add_typer(thesis_app, name="thesis")


def _thesis_table(rows):
    t = Table(title="theses")
    for c in ("id", "status", "venue", "symbol", "size", "qty", "entry", "stop%", "target%", "conf", "expires", "text"):
        t.add_column(c)
    for r in rows:
        color = {"open": "green", "pending": "cyan", "planned": "yellow", "closed": "white", "canceled": "red"}.get(r["status"], "white")
        t.add_row(r["id"], f"[{color}]{r['status']}[/{color}]", r["venue"], r["symbol"], _fmt(r["size_notional"]), _fmt(r["qty"], 6),
                  _fmt(r["entry_price"]), f"{r['stop_pct']:g}", f"{r['target_pct']:g}" if r["target_pct"] else "-", f"{r['confidence']:.2f}",
                  (r["expires_at"] or "")[:10], escape(r["text"][:70]))
    console.print(t)
    if not rows:
        console.print("(no theses)")


@thesis_app.command("open")
def thesis_open(symbol: str, text: str = typer.Option(..., "--text", help="The thesis: catalyst, expected outcome, why"),
                size: float = typer.Option(..., "--size", help="Notional to deploy, in the market currency"),
                stop: float = typer.Option(5.0, "--stop", help="Stop loss percent below entry"),
                target: Optional[float] = typer.Option(None, "--target", help="Take profit percent above entry"),
                expires: Optional[datetime] = typer.Option(None, "--expires", help="Close on/after this date (UTC), e.g. 2026-09-16"),
                confidence: float = typer.Option(0.5, "--confidence", min=0.0, max=1.0),
                venue: Optional[str] = typer.Option(None), market: Optional[Market] = typer.Option(None),
                tag: list[str] = typer.Option([], "--tag"),
                execute: bool = typer.Option(False, "--execute", help="Place the entry order now (default: record only)")):
    """Record a thesis and optionally enter it with a marketable limit order."""
    def run():
        from .models import ThesisRequest
        from datetime import timezone
        exp = expires.replace(tzinfo=timezone.utc) if expires and expires.tzinfo is None else expires
        req = ThesisRequest(symbol=symbol, text=text, size_notional=size, stop_pct=stop, target_pct=target, expires_at=exp,
                            confidence=confidence, venue=venue, market=market, tags=tag)
        t = _engine().open_thesis(req, execute=execute)
        _out(t.model_dump(mode="json"), lambda d: _thesis_table([d]))
    _handle(run)


@thesis_app.command("enter")
def thesis_enter(thesis_id: str):
    """Send the entry order for a planned thesis."""
    def run():
        eng = _engine()
        t = eng.store.get_thesis(thesis_id)
        if not t:
            raise ValueError(f"thesis {thesis_id} not found")
        _out(eng.enter_thesis(t).model_dump(mode="json"), lambda d: _thesis_table([d]))
    _handle(run)


@thesis_app.command("attach")
def thesis_attach(thesis_id: str, qty: Optional[float] = typer.Option(None, "--qty"), entry_price: Optional[float] = typer.Option(None, "--entry"),
                  order_id: Optional[str] = typer.Option(None, "--order-id", help="venue order id, if known")):
    """Mark a planned thesis as open from a fill made elsewhere. Without --qty/--entry, the venue's current position is used."""
    _handle(lambda: _out(_engine().attach_thesis(thesis_id, qty, entry_price, order_id).model_dump(mode="json"), lambda d: _thesis_table([d])))


@thesis_app.command("list")
def thesis_list(all_: bool = typer.Option(False, "--all", help="Include closed and canceled"), venue: Optional[str] = typer.Option(None)):
    """List theses (open, pending and planned by default)."""
    _handle(lambda: _out([t.model_dump(mode="json") for t in _engine().theses(all_, venue)], _thesis_table))


@thesis_app.command("check")
def thesis_check(execute: bool = typer.Option(False, "--execute", help="Actually close theses whose stop/target/expiry is hit"),
                 venue: Optional[str] = typer.Option(None)):
    """Evaluate open theses against live prices; close the ones that hit stop, target or expiry."""
    def run():
        rows = _engine().check_theses(execute=execute, venue=venue)
        def table(rows):
            t = Table(title="thesis check" + ("" if execute else " (dry run)"))
            for c in ("id", "symbol", "status", "last", "entry", "pnl%", "stop", "target", "action", "detail"):
                t.add_column(c)
            for r in rows:
                pnl = r.get("pnl_pct")
                t.add_row(r["id"], r["symbol"], r["status"], _fmt(r.get("last")), _fmt(r.get("entry")),
                          f"[{'green' if (pnl or 0) >= 0 else 'red'}]{pnl:+.2f}[/]" if pnl is not None else "-",
                          _fmt(r.get("stop")), _fmt(r.get("target")), r.get("action") or "", escape(r.get("detail") or ""))
            console.print(t)
            if not rows:
                console.print("(no open theses)")
        _out(rows, table)
    _handle(run)


@thesis_app.command("close")
def thesis_close(thesis_id: str, reason: str = typer.Option("manual close", "--reason"),
                 record_only: bool = typer.Option(False, "--record-only", help="Mark closed without sending an order (exit done elsewhere)"),
                 exit_price: Optional[float] = typer.Option(None, "--exit-price", help="With --record-only: the fill price obtained elsewhere")):
    """Close a thesis now (market order for the held quantity), or record a close executed elsewhere."""
    def run():
        eng = _engine()
        if record_only:
            t = eng.close_thesis(thesis_id, reason=reason, execute=False)
            if exit_price and t.entry_price:
                t.exit_price = exit_price
                t.realized_pnl = (exit_price - t.entry_price) * t.qty
                eng.store.save_thesis(t)
        else:
            t = eng.close_thesis(thesis_id, reason=reason, execute=True)
        _out(t.model_dump(mode="json"), lambda d: _thesis_table([d]))
    _handle(run)


@app.command()
def news(query: Optional[str] = typer.Option(None, "--query", "-q", help="Google News (India) search, e.g. 'Adani' or 'sugar import'"),
         match: Optional[str] = typer.Option(None, "--match", help="comma separated keywords to filter feed headlines"),
         hours: int = typer.Option(36), limit: int = typer.Option(40),
         feeds: Optional[str] = typer.Option(None, help="comma separated feed names (default all): google_business,et_markets,bs_markets,mint_markets,pulse")):
    """Headlines from Indian market feeds, or a Google News search. No credentials needed."""
    def run():
        from .news import scan_headlines, search_news
        if query:
            rows = search_news(query, hours=hours, limit=limit)
        else:
            rows = scan_headlines(match=[m.strip() for m in match.split(",")] if match else None,
                                  feeds=[f.strip() for f in feeds.split(",")] if feeds else None, hours=hours, limit=limit)
        def table(rows):
            t = Table(title=f"news: {query or (match or 'all feeds')} (last {hours}h)")
            for c in ("published", "source", "title"):
                t.add_column(c)
            for r in rows:
                t.add_row((r["published"] or "")[:16], escape(str(r["source"])[:18]), escape(r["title"][:110]))
            console.print(t)
        _out(rows, table)
    _handle(run)


@app.command()
def themes(name: Optional[list[str]] = typer.Option(None, "--name", "-n", help="theme(s); default all"), market: Market = typer.Option(Market.IN),
           members: bool = typer.Option(False, "--members", help="show every member, not just basket summaries")):
    """Where is money moving: returns and volume for actor / policy / macro baskets."""
    def run():
        from .themes import theme_report
        rows = theme_report(_engine(), name or None, market)
        def table(rows):
            t = Table(title="theme baskets")
            for c in ("theme", "n", "avg 1d%", "avg 5d%", "avg 20d%", "max vol/avg20"):
                t.add_column(c, justify="right")
            for r in sorted(rows, key=lambda r: -(r.get("avg_d1") or -999)):
                if r.get("error"):
                    t.add_row(r["theme"], "-", f"[red]{r['error']}[/red]", "", "", "")
                    continue
                t.add_row(r["theme"], str(r["n"]), *(f"{v:+.2f}" if v is not None else "-" for v in (r["avg_d1"], r["avg_d5"], r["avg_d20"])),
                          f"{r['max_vol_ratio']:.1f}x" if r.get("max_vol_ratio") else "-")
            console.print(t)
            if members:
                for r in rows:
                    m = Table(title=f"{r['theme']}")
                    for c in ("symbol", "last", "1d%", "5d%", "20d%", "vol/avg20", "src"):
                        m.add_column(c, justify="right")
                    for x in sorted(r["members"], key=lambda x: -(x.get("d1") or -999)):
                        if "error" in x:
                            m.add_row(x["symbol"], f"[red]{escape(x['error'][:40])}[/red]", "", "", "", "", "")
                            continue
                        m.add_row(x["symbol"], _fmt(x["last"]), *(f"{v:+.2f}" if v is not None else "-" for v in (x["d1"], x["d5"], x["d20"])),
                                  f"{x['vol_ratio']:.1f}x" if x.get("vol_ratio") else "-", x["source"])
                    console.print(m)
        _out(rows, table)
    _handle(run)


@app.command()
def screen(market: Market = typer.Option(Market.IN), top: int = typer.Option(25, help="rows to show"),
           symbol: Optional[list[str]] = typer.Option(None, "--symbol", "-s", help="screen only these symbols"),
           universe: Optional[str] = typer.Option(None, help="file:path override for the universe"),
           benchmark: Optional[str] = typer.Option(None, help="index proxy for alpha/beta (default NSE:NIFTYBEES / SPY / BTC-USD)"),
           lookback: int = typer.Option(260, help="daily bars to load"),
           min_price: float = typer.Option(50.0), min_turnover_cr: float = typer.Option(25.0, help="crore per day"),
           max_momentum_pct: float = typer.Option(40.0, help="drop 20d movers above this (parabolic)"),
           max_extension_pct: float = typer.Option(12.0, help="drop names this far above SMA20"),
           max_rsi: float = typer.Option(78.0), min_bars: int = typer.Option(120),
           beta_bucket: Optional[str] = typer.Option(None, help="low (<0.8) | mid | high (>1.3)"),
           refresh: bool = typer.Option(False, "--refresh", help="ignore today's candle cache"),
           no_save: bool = typer.Option(False, "--no-save", help="do not write data/screens/<date>.json"),
           rejected: bool = typer.Option(False, "--rejected", help="also list rejected symbols and why")):
    """Factor screen (1y alpha/beta vs index, momentum, RSI, volume surge, 52w-high distance) ranked for short-term trades."""
    def run():
        from .screener import screen as run_screen
        def progress(done, total, sym):
            if done % 50 == 0 or done == total:
                err_console.print(f"[dim]screen {done}/{total} {sym}[/dim]")
        res = run_screen(_engine(), market, symbols=symbol or None, universe_spec=universe, benchmark=benchmark, lookback=lookback,
                         min_price=min_price, min_turnover_cr=min_turnover_cr, max_momentum_pct=max_momentum_pct,
                         max_extension_pct=max_extension_pct, max_rsi=max_rsi, min_bars=min_bars, refresh=refresh, save=not no_save,
                         progress=None if _state["json"] else progress)
        rows = res["rows"]
        if beta_bucket:
            rows = [r for r in rows if r.get("beta_bucket") == beta_bucket]
        shown = rows[:top]
        def table(_):
            t = Table(title=f"screen {res['market']} vs {res['benchmark']}: {res['eligible']} eligible of {res['scanned']} "
                            f"(rejected: {', '.join(f'{k} {v}' for k, v in sorted(res['rejected_counts'].items()))})")
            for c in ("#", "symbol", "last", "score", "alpha1y%", "beta", "5d%", "20d%", "60d%", "rsi", "vol5/20", "%52wH", "vol20%", "trend", "turn cr"):
                t.add_column(c, justify="right")
            for r in shown:
                trend = ("A" if r.get("above_sma20") else "-") + ("T" if r.get("sma20_gt_sma50") else "-") + ("R" if r.get("sma50_rising") else "-")
                t.add_row(str(r["rank"]), r["symbol"], _fmt(r.get("last")), _fmt(r.get("score"), 1), f"{r['alpha']:+.1f}", f"{r['beta']:.2f}",
                          *(f"{r[k]:+.1f}" if r.get(k) is not None else "-" for k in ("ret_5", "ret_20", "ret_60")),
                          _fmt(r.get("rsi_14"), 0), f"{r['vol_ratio']:.1f}x" if r.get("vol_ratio") else "-",
                          f"{r['dist_52w_high']:.1f}" if r.get("dist_52w_high") is not None else "-", _fmt(r.get("vol_20"), 0), trend,
                          _fmt(r.get("turnover_cr") or r.get("turnover_cr_20d"), 0))
            console.print(t, width=None if console.is_terminal else max(console.width, 170))
            console.print("[dim]trend: A above SMA20, T SMA20>SMA50, R SMA50 rising. "
                          f"saved: {res.get('saved_to', '-')}[/dim]")
            if rejected:
                rj = Table(title="rejected")
                for c in ("symbol", "why", "last", "20d%"):
                    rj.add_column(c, justify="right")
                for r in res["rejected"]:
                    rj.add_row(r["symbol"], escape(str(r["why"])[:40]), _fmt(r.get("last")), f"{r['ret_20']:+.1f}" if r.get("ret_20") is not None else "-")
                console.print(rj)
        _out({k: v for k, v in res.items() if k not in ("rows", "rejected")} | {"rows": shown} | ({"rejected": res["rejected"]} if rejected else {}), table)
    _handle(run)


portfolio_app = typer.Typer(help="Long-term portfolio: target weights, monthly contribution plan, dividend calendar (portfolio.yaml).",
                            no_args_is_help=True)
app.add_typer(portfolio_app, name="portfolio")


def _pf(model_path):
    from .portfolio import load_model
    eng = _engine()
    return eng, load_model(eng.settings.root, model_path)


@portfolio_app.command("model")
def portfolio_model(model: Optional[str] = typer.Option(None, "--model", help="path to portfolio.yaml")):
    """Show the effective target weight of every holding."""
    def run():
        _eng, m = _pf(model)
        rows = [{"symbol": s, "sleeve": m.sleeve_of(s), "target_w": w} for s, w in sorted(m.targets().items(), key=lambda kv: -kv[1])]
        def table(rows):
            t = Table(title=f"portfolio model ({m.venue}/{m.market.value}); cash buffer {m.cash_buffer:,.0f} {m.currency}")
            for c in ("symbol", "sleeve", "target %"):
                t.add_column(c, justify="right")
            for r in rows:
                t.add_row(r["symbol"], str(r["sleeve"]), f"{r['target_w']*100:.1f}")
            console.print(t)
        _out(rows, table)
    _handle(run)


@portfolio_app.command("status")
def portfolio_status(model: Optional[str] = typer.Option(None, "--model")):
    """Holdings versus targets, drift, and projected dividends by month."""
    def run():
        from .portfolio import status
        eng, m = _pf(model)
        res = status(eng, m)
        def table(res):
            t = Table(title=f"portfolio {res['venue']}: value {res['portfolio_value']:,.0f} {res['currency']}, cash {res['cash']:,.0f}, "
                            f"projected dividend {res['annual_dividend']:,.0f}/yr")
            for c in ("symbol", "sleeve", "qty", "price", "value", "cur %", "tgt %", "drift pp", "yield %", "div/yr"):
                t.add_column(c, justify="right")
            for r in res["rows"]:
                t.add_row(r["symbol"] + (" *" if r["thesis_managed"] else ""), str(r["sleeve"]), _fmt(r["qty"], 0), _fmt(r["price"]), _fmt(r["value"], 0),
                          f"{r['current_w']*100:.1f}", f"{r['target_w']*100:.1f}", f"{r['drift_pp']:+.1f}",
                          f"{r['yield_pct']:.1f}" if r.get("yield_pct") is not None else "-", _fmt(r["annual_dividend"], 0))
            console.print(t)
            months = res["dividends_by_month"]
            console.print("[dim]dividends by month: " + "  ".join(f"{k}:{v:,.0f}" for k, v in months.items() if v) + "   (* = open thesis, not managed here)[/dim]")
            if res.get("not_in_model"):
                console.print(f"[dim]held but not in the model: {', '.join(res['not_in_model'])}[/dim]")
        _out(res, table)
    _handle(run)


@portfolio_app.command("plan")
def portfolio_plan(contribution: float = typer.Option(..., "--contribution", "-c", help="new money to deploy, in the model currency"),
                   model: Optional[str] = typer.Option(None, "--model"),
                   execute: bool = typer.Option(False, "--execute", help="place the buys as limit orders (risk-checked)"),
                   dry_run: bool = typer.Option(False, "--dry-run", help="with --execute: risk checks only")):
    """Turn a contribution into a buy list that fills the largest gaps to target first (never sells)."""
    def run():
        from .portfolio import execute_plan, plan
        eng, m = _pf(model)
        res = plan(eng, m, contribution)
        if execute:
            res["orders"] = execute_plan(eng, m, res, dry_run=dry_run)
        def table(res):
            t = Table(title=f"portfolio plan {res['as_of']}: contribution {res['contribution']:,.0f} + idle {max(0, res['idle_cash']-res['cash_buffer']):,.0f} "
                            f"= budget {res['budget']:,.0f} {res['currency']}; spend {res['spend']:,.0f}, unspent {res['unspent']:,.0f}")
            for c in ("symbol", "sleeve", "qty", "limit", "notional", "gap before", "record soon", "risk"):
                t.add_column(c, justify="right")
            for b in res["buys"]:
                t.add_row(b["symbol"], str(b["sleeve"]), str(b["qty"]), _fmt(b["limit"]), _fmt(b["notional"], 0), _fmt(b["gap_before"], 0),
                          "yes" if b["record_soon"] else "", escape("; ".join(b["risk_flags"])) if b["risk_flags"] else "")
            console.print(t)
            if res["skipped"]:
                console.print("[dim]skipped: " + "; ".join(f"{s['symbol']} ({s['why']})" for s in res["skipped"]) + "[/dim]")
            if res.get("orders"):
                o = Table(title="orders")
                for c in ("symbol", "qty", "limit", "status", "detail"):
                    o.add_column(c, justify="right")
                for r in res["orders"]:
                    o.add_row(r["symbol"], str(r["qty"]), _fmt(r["limit"]), r["status"], escape(str(r.get("error") or r.get("order_id") or "")))
                console.print(o)
        _out(res, table)
    _handle(run)


@portfolio_app.command("calendar")
def portfolio_calendar(months: int = typer.Option(3, "--months"), model: Optional[str] = typer.Option(None, "--model")):
    """Upcoming payouts for held names: expected credits and the amount to add back next month."""
    def run():
        from .portfolio import calendar
        eng, m = _pf(model)
        res = calendar(eng, m, months=months)
        def table(res):
            t = Table(title=f"dividend calendar from {res['as_of']}, next {res['months']} months: expected {res['expected_total']:,.0f}")
            for c in ("symbol", "pay month", "record month", "dps", "qty held", "expected", "note"):
                t.add_column(c, justify="right")
            for e in res["events"]:
                t.add_row(e["symbol"], str(e["pay_month"]), str(e["record_month"]), _fmt(e["dps"]), _fmt(e["qty_held"], 0), _fmt(e["expected"], 0),
                          escape(str(e.get("note") or e.get("buy_before") or "")))
            console.print(t)
            console.print(f"[dim]{res['reinvest_note']}[/dim]")
        _out(res, table)
    _handle(run)


universe_app = typer.Typer(help="Build and inspect liquidity-screened universes.", no_args_is_help=True)
app.add_typer(universe_app, name="universe")


@universe_app.command("build")
def universe_build(market: Market = typer.Option(Market.IN), min_turnover_cr: float = typer.Option(25.0, help="min daily turnover, crore INR"),
                   max_price: Optional[float] = typer.Option(None, help="drop shares priced above this")):
    """Screen every NSE equity by turnover (needs a Kite token) and write data/universe/in.json."""
    def run():
        from .universe import build_in_universe
        if market != Market.IN:
            raise ValueError("only the Indian market universe builder is implemented")
        out = build_in_universe(_engine(), min_turnover_cr, max_price)
        _out({k: v for k, v in out.items() if k != "rows"} | {"top": out["rows"][:15]})
    _handle(run)


@universe_app.command("show")
def universe_show(market: Market = typer.Option(Market.IN), limit: int = typer.Option(50)):
    """Show the saved universe."""
    def run():
        import json
        from pathlib import Path
        eng = _engine()
        p = Path(eng.settings.root) / f"data/universe/{market.value}.json"
        if not p.exists():
            raise ValueError(f"{p} not found; run: tradebot universe build")
        d = json.loads(p.read_text())
        _out({k: v for k, v in d.items() if k != "rows"} | {"rows": d["rows"][:limit]})
    _handle(run)


@app.command()
def hours():
    """Show whether each market session is open and when it next opens."""
    from .hours import market_session
    _out([market_session(m) for m in Market])


@app.command("export")
def export_state(out: Optional[str] = typer.Option(None, "--out", help="file path (default data/snapshots/<date>.json)")):
    """Dump journal, theses, orders, fills and equity curve to JSON."""
    def run():
        from pathlib import Path
        eng = _engine()
        data = eng.export_state()
        path = Path(out) if out else Path(eng.settings.root) / "data" / "snapshots" / f"{datetime.utcnow():%Y-%m-%d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, default=str))
        _out({"written": str(path), "journal": len(data["journal"]), "theses": len(data["theses"]), "orders": len(data["orders"])})
    _handle(run)


@app.command("import")
def import_state(path: str, force: bool = typer.Option(False, "--force", help="Replace every thesis in the snapshot even if ours is newer")):
    """Restore theses, order history and journal from an export (idempotent; newer thesis record wins)."""
    def run():
        from pathlib import Path
        data = json.loads(Path(path).read_text())
        _out(_engine().import_state(data, force=force))
    _handle(run)


@app.command()
def serve(host: Optional[str] = typer.Option(None), port: Optional[int] = typer.Option(None), reload: bool = typer.Option(False)):
    """Run the HTTP API + dashboard."""
    import uvicorn
    from .config import load_settings
    s = load_settings(_state["config"])
    uvicorn.run("tradebot.api.app:app", host=host or s.api_host, port=port or s.api_port, reload=reload)


if __name__ == "__main__":
    app()
