"""FastAPI service exposing the engine, plus a small dashboard at ``/``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..config import load_settings
from ..engine import TradingEngine
from ..errors import NotFound, RiskRejected, TradebotError
from ..models import Market, OrderRequest, ThesisRequest

app = FastAPI(title="tradebot", version=__version__)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
_engine: Optional[TradingEngine] = None


def engine() -> TradingEngine:
    global _engine
    if _engine is None:
        _engine = TradingEngine(load_settings(os.environ.get("TRADEBOT_CONFIG")))
    return _engine


def auth(authorization: Optional[str] = Header(None)) -> None:
    token = engine().settings.api_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "invalid or missing bearer token")


@app.exception_handler(TradebotError)
async def _tb_error(_, exc: TradebotError):
    status = 404 if isinstance(exc, NotFound) else 422 if isinstance(exc, RiskRejected) else 400
    return JSONResponse(status_code=status, content=exc.to_dict())


@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/doctor", dependencies=[Depends(auth)])
def doctor(data: bool = True):
    return engine().doctor(include_data=data)


@app.get("/venues", dependencies=[Depends(auth)])
def venues():
    e = engine()
    return [{"venue": n, "configured": e.brokers.get(n).available(), "live": e.brokers.get(n).live,
             "markets": [m.value for m in e.brokers.get(n).markets]} for n in e.brokers.names()]


@app.get("/quote/{symbol}", dependencies=[Depends(auth)])
def quote(symbol: str, market: Optional[Market] = None, provider: Optional[str] = None):
    return engine().quote(symbol, market, provider)


@app.get("/quotes", dependencies=[Depends(auth)])
def quotes(symbols: str = Query(..., description="comma separated"), market: Optional[Market] = None):
    return engine().quotes([s for s in symbols.split(",") if s.strip()], market)


@app.get("/candles/{symbol}", dependencies=[Depends(auth)])
def candles(symbol: str, interval: str = "1d", limit: int = 100, market: Optional[Market] = None, provider: Optional[str] = None):
    rows, src = engine().candles(symbol, interval, limit, market, provider=provider)
    return {"symbol": engine().instrument(symbol, market).symbol, "interval": interval, "source": src, "candles": rows}


@app.get("/search", dependencies=[Depends(auth)])
def search(q: str, market: Market = Market.IN):
    return engine().search(q, market)


@app.get("/account", dependencies=[Depends(auth)])
def account(venue: Optional[str] = None, market: Optional[Market] = None):
    return engine().pnl(venue, market)


@app.get("/positions", dependencies=[Depends(auth)])
def positions(venue: Optional[str] = None, market: Optional[Market] = None):
    return engine().positions(venue, market)


@app.get("/orders", dependencies=[Depends(auth)])
def orders(venue: Optional[str] = None, symbol: Optional[str] = None, open: bool = False, limit: int = 50, refresh: bool = False):
    return engine().orders(venue, symbol, open, limit, refresh=refresh)


@app.get("/orders/{order_id}", dependencies=[Depends(auth)])
def get_order(order_id: str):
    return engine().order(order_id)


@app.post("/orders", dependencies=[Depends(auth)], status_code=201)
def place_order(req: OrderRequest, dry_run: bool = False):
    return engine().place_order(req, dry_run=dry_run)


@app.delete("/orders/{order_id}", dependencies=[Depends(auth)])
def cancel_order(order_id: str):
    return engine().cancel_order(order_id)


@app.delete("/orders", dependencies=[Depends(auth)])
def cancel_all(venue: Optional[str] = None, symbol: Optional[str] = None):
    return engine().cancel_all(venue, symbol)


class CloseReq(BaseModel):
    symbol: str
    venue: Optional[str] = None
    market: Optional[Market] = None
    reason: Optional[str] = None


@app.post("/close", dependencies=[Depends(auth)])
def close(req: CloseReq):
    return engine().close_position(req.symbol, req.venue, req.market, req.reason)


@app.post("/sync", dependencies=[Depends(auth)])
def sync(venue: Optional[str] = None):
    return engine().sync(venue)


@app.get("/equity", dependencies=[Depends(auth)])
def equity(venue: str = "paper", market: Market = Market.CRYPTO, limit: int = 2000):
    return engine().equity_curve(venue, market, limit)


@app.get("/fills", dependencies=[Depends(auth)])
def fills(venue: Optional[str] = None, symbol: Optional[str] = None, limit: int = 200):
    return engine().store.list_fills(venue, symbol, limit=limit)


@app.get("/journal", dependencies=[Depends(auth)])
def journal(limit: int = 50, kind: Optional[str] = None, symbol: Optional[str] = None):
    return engine().journal(limit, kind, symbol)


class NoteReq(BaseModel):
    text: str
    symbol: Optional[str] = None
    data: Optional[dict] = None


@app.post("/journal", dependencies=[Depends(auth)], status_code=201)
def note(req: NoteReq):
    return engine().note(req.text, req.symbol, req.data)


class KillReq(BaseModel):
    on: bool = True


@app.post("/kill", dependencies=[Depends(auth)])
def kill(req: KillReq):
    return {"kill_switch_active": engine().set_kill_switch(req.on)}


@app.get("/theses", dependencies=[Depends(auth)])
def theses(all: bool = False, venue: Optional[str] = None):
    return engine().theses(all, venue)


@app.post("/theses", dependencies=[Depends(auth)], status_code=201)
def open_thesis(req: ThesisRequest, execute: bool = False):
    return engine().open_thesis(req, execute=execute)


@app.post("/theses/check", dependencies=[Depends(auth)])
def check_theses(execute: bool = False, venue: Optional[str] = None):
    return engine().check_theses(execute=execute, venue=venue)


@app.delete("/theses/{thesis_id}", dependencies=[Depends(auth)])
def close_thesis(thesis_id: str, reason: str = "manual close"):
    return engine().close_thesis(thesis_id, reason=reason, execute=True)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return (Path(__file__).parent / "static" / "index.html").read_text()
