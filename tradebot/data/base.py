from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import httpx

from ..errors import DataError
from ..models import Candle, Instrument, Market, Quote

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Canonical interval names accepted by the CLI/API.
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")


def http_client(timeout: float = 20.0, headers: Optional[dict] = None) -> httpx.Client:
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    # httpx honours HTTPS_PROXY / SSL_CERT_FILE from the environment by default.
    return httpx.Client(timeout=timeout, headers=h, follow_redirects=True)


class MarketDataProvider(ABC):
    name: str = "base"
    markets: tuple[Market, ...] = ()
    requires_credentials: bool = False

    def __init__(self, settings=None):
        self.settings = settings
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = http_client()
        return self._client

    def available(self) -> bool:
        """True if the provider can be used (credentials present etc.)."""
        return True

    @abstractmethod
    def quote(self, inst: Instrument) -> Quote: ...

    @abstractmethod
    def candles(self, inst: Instrument, interval: str = "1d", limit: int = 100,
                start: Optional[datetime] = None, end: Optional[datetime] = None) -> list[Candle]: ...

    def ping(self) -> tuple[bool, str, int]:
        """Connectivity check. Returns (ok, detail, latency_ms)."""
        t0 = time.time()
        try:
            detail = self._ping()
            return True, detail, int((time.time() - t0) * 1000)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"[:200], int((time.time() - t0) * 1000)

    def _ping(self) -> str:
        return "ok"

    @staticmethod
    def _f(v, default: Optional[float] = None) -> Optional[float]:
        if v is None or v == "":
            return default
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").replace("₹", "").strip()
            if v in ("", "N/A", "NA", "--"):
                return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _get_json(self, url: str, **kw):
        r = self.client.get(url, **kw)
        if r.status_code >= 400:
            raise DataError(f"{self.name}: HTTP {r.status_code} for {url}", details={"body": r.text[:300]})
        try:
            return r.json()
        except ValueError as e:
            raise DataError(f"{self.name}: non-JSON response from {url}") from e
