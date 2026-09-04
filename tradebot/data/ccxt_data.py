"""Generic crypto market data via CCXT (public endpoints, no credentials).

The exchange is taken from ``ccxt.exchange`` in config (default kraken)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote
from .base import MarketDataProvider

TF = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}


def make_exchange(exchange_id: str, api_key: str | None = None, secret: str | None = None, password: str | None = None,
                  sandbox: bool = False, default_type: str = "spot"):
    import ccxt
    if not hasattr(ccxt, exchange_id):
        raise DataError(f"ccxt: unknown exchange {exchange_id}")
    opts = {"timeout": 20_000, "enableRateLimit": True, "options": {"defaultType": default_type}}
    if api_key:
        opts.update({"apiKey": api_key, "secret": secret or ""})
        if password:
            opts["password"] = password
    ex = getattr(ccxt, exchange_id)(opts)
    # ccxt disables trust_env; re-enable so HTTPS_PROXY / REQUESTS_CA_BUNDLE apply in proxied sandboxes.
    ex.session.trust_env = True
    if os.environ.get("HTTPS_PROXY"):
        ex.session.proxies = {"https": os.environ["HTTPS_PROXY"], "http": os.environ.get("HTTP_PROXY", os.environ["HTTPS_PROXY"])}
    if os.environ.get("REQUESTS_CA_BUNDLE"):
        ex.session.verify = os.environ["REQUESTS_CA_BUNDLE"]
    if sandbox:
        ex.set_sandbox_mode(True)
    return ex


class CcxtData(MarketDataProvider):
    name = "ccxt"
    markets = (Market.CRYPTO,)

    def __init__(self, settings=None):
        super().__init__(settings)
        self.exchange_id = settings.ccxt.exchange if settings else "kraken"
        self._ex = None

    @property
    def ex(self):
        if self._ex is None:
            self._ex = make_exchange(self.exchange_id)
        return self._ex

    @staticmethod
    def _sym(inst: Instrument) -> str:
        return f"{inst.base}/{inst.currency}"

    def _ping(self) -> str:
        t = self.ex.fetch_ticker("BTC/USD" if self.exchange_id in ("kraken", "coinbase", "gemini", "coinbaseexchange") else "BTC/USDT")
        return f"{self.exchange_id} {t['symbol']} last={t['last']}"

    def quote(self, inst: Instrument) -> Quote:
        try:
            t = self.ex.fetch_ticker(self._sym(inst))
        except Exception as e:  # noqa: BLE001
            raise DataError(f"ccxt/{self.exchange_id}: {e}") from e
        ts = datetime.fromtimestamp(t["timestamp"] / 1000, tz=timezone.utc) if t.get("timestamp") else datetime.now(timezone.utc)
        return Quote(symbol=inst.symbol, market=inst.market, currency=inst.currency, last=float(t["last"]),
                     bid=t.get("bid"), ask=t.get("ask"), open=t.get("open"), high=t.get("high"), low=t.get("low"),
                     prev_close=t.get("previousClose") or t.get("open"), volume=t.get("baseVolume"), ts=ts,
                     source=f"ccxt:{self.exchange_id}")

    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]:
        if interval not in TF:
            raise DataError(f"ccxt: unsupported interval {interval}")
        since = int(start.timestamp() * 1000) if start else None
        try:
            rows = self.ex.fetch_ohlcv(self._sym(inst), TF[interval], since=since, limit=limit)
        except Exception as e:  # noqa: BLE001
            raise DataError(f"ccxt/{self.exchange_id}: {e}") from e
        out = [Candle(ts=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc), open=r[1], high=r[2], low=r[3], close=r[4],
                      volume=r[5] or 0.0) for r in rows]
        if end:
            out = [c for c in out if c.ts <= end]
        return out[-limit:]
