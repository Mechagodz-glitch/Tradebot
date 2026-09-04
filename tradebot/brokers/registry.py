from __future__ import annotations

from ..errors import ConfigError
from .alpaca import AlpacaBroker
from .base import Broker
from .ccxt_broker import CcxtBroker
from .kite import KiteBroker
from .paper import PaperBroker

BROKERS: dict[str, type[Broker]] = {"paper": PaperBroker, "alpaca": AlpacaBroker, "kite": KiteBroker, "ccxt": CcxtBroker}


class BrokerRegistry:
    def __init__(self, settings, store, data):
        self.settings, self.store, self.data = settings, store, data
        self._instances: dict[str, Broker] = {}

    def names(self) -> list[str]:
        return list(BROKERS)

    def get(self, name: str) -> Broker:
        if name not in BROKERS:
            raise ConfigError(f"unknown venue {name!r}; known venues: {sorted(BROKERS)}")
        if name not in self._instances:
            self._instances[name] = BROKERS[name](self.settings, self.store, self.data)
        return self._instances[name]

    @property
    def paper(self) -> PaperBroker:
        return self.get("paper")  # type: ignore[return-value]
