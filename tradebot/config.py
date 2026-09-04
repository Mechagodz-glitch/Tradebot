"""Configuration: ``config.yaml`` for behaviour, environment variables for secrets.

Secrets are never written to config.yaml. Look up order:
  1. explicit ``--config`` path / TRADEBOT_CONFIG env var
  2. ./config.yaml
  3. built-in defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .models import Market


class PaperConfig(BaseModel):
    starting_cash: dict[str, float] = Field(default_factory=lambda: {"us": 100_000, "in": 1_000_000, "crypto": 100_000})
    slippage_bps: float = 5.0
    fee_bps: dict[str, float] = Field(default_factory=lambda: {"us": 0.0, "in": 3.0, "crypto": 40.0})
    allow_short: bool = False


class RiskConfig(BaseModel):
    kill_switch_file: str = "data/KILL"
    allowed_markets: list[str] = Field(default_factory=lambda: ["us", "in", "crypto"])
    allowed_symbols: Optional[list[str] | dict[str, list[str]]] = None  # None = any; list = global; dict = per market
    allow_outside_hours: bool = False  # live venues: reject orders when the exchange session is closed
    blocked_symbols: list[str] = Field(default_factory=list)
    max_order_notional: dict[str, float] = Field(default_factory=lambda: {"USD": 5_000, "INR": 200_000})
    max_position_notional: dict[str, float] = Field(default_factory=lambda: {"USD": 20_000, "INR": 1_000_000})
    max_daily_loss: dict[str, float] = Field(default_factory=lambda: {"USD": 1_000, "INR": 50_000})
    max_open_orders: int = 20
    max_orders_per_minute: int = 10


class AlpacaConfig(BaseModel):
    paper: bool = True


class CcxtConfig(BaseModel):
    exchange: str = "kraken"
    sandbox: bool = False
    default_type: str = "spot"


class KiteConfig(BaseModel):
    product: str = "CNC"  # CNC delivery, MIS intraday
    exchange: str = "NSE"


class StrategyConfig(BaseModel):
    name: str = "trend"
    universe: dict[str, list[str]] = Field(default_factory=lambda: {
        "in": ["NSE:RELIANCE", "NSE:HDFCBANK", "NSE:ICICIBANK", "NSE:INFY", "NSE:TCS", "NSE:SBIN", "NSE:ITC",
               "NSE:BHARTIARTL", "NSE:KOTAKBANK", "NSE:LT"],
        "us": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "V", "XOM", "UNH"],
        "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "LTC-USD"],
    })
    max_positions: int = 3
    position_fraction: float = 0.30      # of account equity per position
    cash_buffer_fraction: float = 0.05   # never deploy the last 5% of equity
    stop_loss_pct: float = 3.0           # exit if price < avg entry * (1 - pct/100)
    fast_sma: int = 20
    slow_sma: int = 50
    momentum_days: int = 20
    min_history: int = 55
    entry_limit_offset_bps: float = 15.0 # buy limit = reference * (1 + bps/1e4): marketable, bounded slippage
    exit_order_type: str = "market"


class DataConfig(BaseModel):
    us: list[str] = Field(default_factory=lambda: ["nasdaq", "alpaca"])
    in_: list[str] = Field(default_factory=lambda: ["kite", "groww", "upstox"], alias="in")
    crypto: list[str] = Field(default_factory=lambda: ["coinbase", "kraken", "ccxt"])
    cache_dir: str = "data/cache"
    quote_ttl_seconds: float = 2.0

    model_config = {"populate_by_name": True}

    def providers_for(self, market: Market) -> list[str]:
        return {Market.US: self.us, Market.IN: self.in_, Market.CRYPTO: self.crypto}[market]


class Settings(BaseModel):
    db_path: str = "data/tradebot.db"
    default_venue: str = "paper"
    live_trading_enabled: bool = False
    paper: PaperConfig = Field(default_factory=PaperConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    alpaca: AlpacaConfig = Field(default_factory=AlpacaConfig)
    ccxt: CcxtConfig = Field(default_factory=CcxtConfig)
    kite: KiteConfig = Field(default_factory=KiteConfig)
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    # secrets (env only)
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None
    kite_api_key: Optional[str] = None
    kite_api_secret: Optional[str] = None
    kite_access_token: Optional[str] = None
    ccxt_api_key: Optional[str] = None
    ccxt_secret: Optional[str] = None
    ccxt_password: Optional[str] = None
    api_token: Optional[str] = None  # optional bearer token for the HTTP API

    config_path: Optional[str] = None
    root: str = "."

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else Path(self.root) / path


_ENV_MAP = {
    "alpaca_api_key": "ALPACA_API_KEY",
    "alpaca_secret_key": "ALPACA_SECRET_KEY",
    "kite_api_key": "KITE_API_KEY",
    "kite_api_secret": "KITE_API_SECRET",
    "kite_access_token": "KITE_ACCESS_TOKEN",
    "ccxt_api_key": "CCXT_API_KEY",
    "ccxt_secret": "CCXT_SECRET",
    "ccxt_password": "CCXT_PASSWORD",
    "api_token": "TRADEBOT_API_TOKEN",
}


def load_settings(config_path: str | None = None, root: str | None = None) -> Settings:
    root_dir = Path(root or os.environ.get("TRADEBOT_ROOT") or ".").resolve()
    load_dotenv(root_dir / ".env", override=False)

    path = config_path or os.environ.get("TRADEBOT_CONFIG")
    candidates = [Path(path)] if path else [root_dir / "config.yaml"]
    raw: dict = {}
    used: Optional[str] = None
    for cand in candidates:
        cand = cand if cand.is_absolute() else root_dir / cand
        if cand.exists():
            with open(cand) as fh:
                raw = yaml.safe_load(fh) or {}
            used = str(cand)
            break
        elif path:
            raise FileNotFoundError(f"config file not found: {cand}")

    settings = Settings.model_validate(raw)
    settings.config_path = used
    settings.root = str(root_dir)
    for attr, env in _ENV_MAP.items():
        val = os.environ.get(env)
        if val:
            setattr(settings, attr, val)
    if os.environ.get("TRADEBOT_LIVE") in ("1", "true", "yes"):
        settings.live_trading_enabled = True
    if os.environ.get("TRADEBOT_DB"):
        settings.db_path = os.environ["TRADEBOT_DB"]
    if os.environ.get("ALPACA_PAPER") in ("0", "false", "no"):
        settings.alpaca.paper = False
    return settings
