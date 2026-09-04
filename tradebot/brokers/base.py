from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional

from ..models import Account, CheckResult, Instrument, Market, Order, OrderRequest, Position


class Broker(ABC):
    """Execution venue interface. ``live`` is True when real money can move."""

    name: str = "base"
    live: bool = False
    markets: tuple[Market, ...] = ()

    def __init__(self, settings, store, data):
        self.settings = settings
        self.store = store
        self.data = data

    def available(self) -> bool:
        return True

    def supports(self, market: Market) -> bool:
        return market in self.markets

    @abstractmethod
    def account(self, market: Market) -> Account: ...

    @abstractmethod
    def positions(self, market: Optional[Market] = None, mark: bool = True) -> list[Position]: ...

    @abstractmethod
    def place_order(self, req: OrderRequest, inst: Instrument) -> Order: ...

    @abstractmethod
    def cancel_order(self, order: Order) -> Order: ...

    def refresh_order(self, order: Order) -> Order:
        """Re-read venue state for a mirrored order. Paper orders are always current."""
        return order

    def sync(self) -> dict:
        """Process resting orders / refresh mirrors. Returns a summary dict."""
        return {}

    def check(self) -> CheckResult:
        t0 = time.time()
        try:
            detail = self._check()
            return CheckResult(name=f"broker:{self.name}", ok=True, detail=detail, latency_ms=int((time.time() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            return CheckResult(name=f"broker:{self.name}", ok=False, detail=f"{type(e).__name__}: {e}"[:300],
                               latency_ms=int((time.time() - t0) * 1000))

    def _check(self) -> str:
        return "ok"
