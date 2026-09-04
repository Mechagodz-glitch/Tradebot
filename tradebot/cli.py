"""Command line interface. Every command accepts ``--json`` for machine readable output."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
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
                  r["created_at"][:19], (r.get("reject_reason") or r.get("reason") or "")[:60])
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
                t.add_row(r["ts"][:19], r["kind"], r.get("venue") or "", r.get("symbol") or "", r.get("order_id") or "", r["text"])
            console.print(t)
        _out(rows, table)
    _handle(run)


@app.command()
def note(text: str, symbol: Optional[str] = typer.Option(None)):
    """Add a free-text note to the journal (e.g. a thesis or observation)."""
    _handle(lambda: _out(_engine().note(text, symbol).model_dump(mode="json")))


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


@app.command("kite-login")
def kite_login(request_token: Optional[str] = typer.Argument(None)):
    """Zerodha login: print the login URL, then exchange the request_token for an access token."""
    def run():
        eng = _engine()
        kite = eng.brokers.get("kite")
        if not request_token:
            _out({"login_url": kite.login_url(), "next": "log in, copy request_token from the redirect URL, run: tradebot kite-login <request_token>"})
        else:
            tok = kite.exchange_token(request_token)
            _out({"access_token": tok, "next": "export KITE_ACCESS_TOKEN=<token> (valid until ~6am IST next day)"})
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
