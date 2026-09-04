# Tradebot

A deterministic execution layer for paper and live trading across three markets, designed to sit
underneath an AI agent (or a human) that makes the decisions. The agent issues commands; this layer
handles market data, risk checks, order routing, persistence and reporting.

| Market | Symbol form | Currency | Public data (no keys) | Live venues |
|---|---|---|---|---|
| US equities (`us`) | `AAPL` | USD | Nasdaq quotes and daily candles | Alpaca (paper and live) |
| Indian equities (`in`) | `NSE:RELIANCE` | INR | Groww quotes, Upstox candles and instrument master | Zerodha Kite Connect |
| Crypto (`crypto`) | `BTC-USD` | USD (or any quote ccy) | Coinbase Exchange, Kraken, any CCXT exchange | Alpaca (crypto), any CCXT exchange, Coinbase/Kraken via CCXT |

The built-in `paper` venue works for all three markets with no credentials. It fills at live bid/ask
with configurable slippage and fees, tracks cash, positions, realized and unrealized P&L per market, and
persists everything in SQLite.

## Quick start

```bash
uv venv .venv --python 3.11 && . .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env            # optional; only needed for live venues
tradebot doctor                 # connectivity + credentials check
tradebot quote BTC-USD AAPL NSE:RELIANCE
tradebot buy BTC-USD 0.01 --reason "test entry"
tradebot positions
tradebot account
tradebot serve                  # HTTP API + dashboard at http://127.0.0.1:8787
pytest -q
```

Every command accepts `--json` (placed before the command) for machine readable output:

```bash
tradebot --json buy AAPL 5 --type limit --limit 300 --tif gtc --reason "buy the dip"
```

## CLI reference

| Command | Purpose |
|---|---|
| `doctor [--no-data]` | Check config, kill switch, live gate, every data provider and every configured venue |
| `venues` | List venues, whether credentials are present, and whether they move real money |
| `quote SYM...` | Latest quote. `--market us/in/crypto` overrides symbol detection, `--provider` forces a source |
| `candles SYM --interval 1d --limit 30` | OHLCV history. Intervals: 1m 5m 15m 30m 1h 4h 1d 1w (availability varies by provider) |
| `search TEXT` | Instrument search (Indian market, Upstox master) |
| `account [--venue] [--market]` | Cash, equity, realized/unrealized, total and day P&L |
| `positions [--venue] [--market]` | Open positions with live marks |
| `orders [--open] [--symbol] [--limit] [--refresh]` | Order history, newest first |
| `order ID` | One order by tradebot id, venue id or client id |
| `buy SYM QTY` / `sell SYM QTY` | Place an order. Options: `--type market/limit/stop/stop_limit`, `--limit`, `--stop`, `--tif day/gtc/ioc`, `--venue`, `--market`, `--reason`, `--strategy`, `--client-id`, `--dry-run` |
| `cancel ID` / `cancel --all [--venue] [--symbol]` | Cancel open orders |
| `close SYM` | Flatten a position with a market order |
| `sync [--venue]` | Fill resting paper orders against live quotes, refresh live orders, record an equity snapshot |
| `journal [--kind order/fill/risk/note/system] [--symbol]` | Audit trail including the `--reason` given for every order |
| `note TEXT [--symbol]` | Free text journal entry |
| `kill` / `kill --off` | Engage or release the kill switch (all new orders rejected while on) |
| `reset-paper --yes [--market]` | Wipe paper state back to starting cash |
| `kite-login [REQUEST_TOKEN]` | Zerodha daily login flow |
| `serve [--host] [--port]` | Run the HTTP API and dashboard |

Exit codes: 0 ok, 2 application error (risk rejection, bad symbol, venue error), 3 order rejected by the venue.

## HTTP API

`tradebot serve` starts FastAPI on `api_host:api_port` (default 127.0.0.1:8787). OpenAPI docs at `/docs`.
Set `TRADEBOT_API_TOKEN` to require `Authorization: Bearer <token>` on every endpoint except `/health`.

| Method and path | Purpose |
|---|---|
| `GET /health`, `GET /doctor`, `GET /venues` | Diagnostics |
| `GET /quote/{symbol}`, `GET /quotes?symbols=a,b`, `GET /candles/{symbol}?interval=1d&limit=100`, `GET /search?q=` | Market data |
| `GET /account`, `GET /positions`, `GET /orders`, `GET /orders/{id}`, `GET /fills`, `GET /equity` | State |
| `POST /orders` (body: OrderRequest, `?dry_run=true` for checks only) | Place order |
| `DELETE /orders/{id}`, `DELETE /orders?venue=&symbol=` | Cancel |
| `POST /close` `{symbol, venue?, reason?}` | Flatten |
| `POST /sync?venue=` | Process resting orders and snapshot equity |
| `GET /journal`, `POST /journal` `{text, symbol?, data?}` | Journal |
| `POST /kill` `{on: true/false}` | Kill switch |
| `GET /` | Dashboard: equity curve, KPIs, positions, orders, journal, quote lookup |

Errors are JSON: `{"error": "<category>", "code": "<specific reason>", "message": "...", "details": {...}}`.
Risk rejections return HTTP 422, not-found 404, other application errors 400.

## Safety model

Every order, on every venue, passes through the risk engine before it reaches a broker:

1. **Kill switch**: if `data/KILL` exists, everything is rejected. `tradebot kill` creates it.
2. **Live gate**: venues that move real money (`kite`, `ccxt`, Alpaca with `alpaca.paper: false`) refuse
   orders unless `live_trading_enabled: true` in `config.yaml` or `TRADEBOT_LIVE=1`. Default is off.
3. **Allowed markets and symbols**, blocked symbols.
4. **Max order notional** and **max resulting position notional** per currency (USD and INR limits).
5. **Max daily loss** per currency: once equity is down by this much from the day's opening equity,
   only position-reducing orders are accepted.
6. **Max open orders** and **max orders per minute** (venue wide).

Rejections are journaled with kind `risk` and the machine readable code.

## Configuration

`config.yaml` holds behaviour (venue defaults, paper cash and fees, risk limits, data provider order).
`.env` holds secrets. See `config.yaml` and `.env.example`; every key is commented.

Data provider order per market is configurable. The first provider that answers wins; failures fall
through to the next. Quotes are cached for `quote_ttl_seconds` (default 2s).

## Venue setup

**Paper** (default): nothing to do. Starting cash: 100,000 USD (us), 1,000,000 INR (in), 100,000 USD (crypto).

**Alpaca** (US stocks and crypto): create a free account at alpaca.markets, generate paper API keys,
set `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. Paper trading is the default and is not gated by the live
switch. Set `ALPACA_PAPER=false` plus `live_trading_enabled: true` to trade live. Also unlocks Alpaca
market data (IEX feed, intraday candles) as a US and crypto data provider.

**Zerodha Kite Connect** (Indian equities): requires a Kite Connect developer app (paid subscription from
Zerodha) giving `KITE_API_KEY` and `KITE_API_SECRET`. Kite issues a new access token every day through a
browser login: run `tradebot kite-login`, open the URL, log in, copy `request_token` from the redirect
URL, run `tradebot kite-login <request_token>` and export the printed `KITE_ACCESS_TOKEN`. Quantities
must be whole shares. Product type defaults to CNC (delivery); set `kite.product: MIS` for intraday.

**CCXT** (any crypto exchange): set `ccxt.exchange` in `config.yaml` to the CCXT id (`kraken`,
`coinbase`, `okx`, `kucoin`, `delta`, `bitget`, `gemini`, ...) and `CCXT_API_KEY` / `CCXT_SECRET`
(`CCXT_PASSWORD` for exchanges with a passphrase). `ccxt.sandbox: true` uses the exchange's test
environment where one exists. Public market data through CCXT needs no keys.

## Connectivity verified from the development sandbox (2026-09-04)

| Venue / source | Reachable | Notes |
|---|---|---|
| Coinbase Exchange, Kraken, OKX, KuCoin, Bitget, Gemini, CoinGecko | yes | public data works without keys |
| Delta Exchange India, CoinDCX, WazirX | yes | public tickers work; execution via CCXT (`delta`) |
| Binance (and Binance testnet), Bybit | **no** | HTTP 451 / 403: geo-restricted from this egress |
| Alpaca paper and data | yes | needs free API keys |
| Tradier sandbox | yes | not integrated |
| Zerodha Kite, Upstox, Angel One, Dhan | yes | Kite instrument master public; orders need paid API access |
| Nasdaq quote API, Groww, Upstox candles | yes | used as free data sources |
| Yahoo Finance | rate limited (429) | not used |

## Layout

```
tradebot/
  models.py        pydantic models: Instrument, Quote, Candle, OrderRequest, Order, Fill, Position, Account
  symbols.py       canonical symbol parsing (AAPL, NSE:RELIANCE, BTC-USD)
  config.py        config.yaml + .env loader
  store.py         SQLite persistence (orders, fills, positions, accounts, equity, journal)
  risk.py          pre-trade risk engine
  engine.py        TradingEngine: the one entry point used by CLI and API
  cli.py           Typer CLI
  data/            market data providers + registry with fallback
  brokers/         paper, alpaca, kite, ccxt adapters behind one Broker interface
  api/             FastAPI app + dashboard (static/index.html)
tests/             pytest suite using a deterministic fake data provider (no network)
AGENT.md           operating contract for the AI layer that drives this tool
```

## Limitations

- Paper fills are simplified: full fill at bid/ask plus slippage, no partial fills, no market hours
  enforcement (a paper order placed when NSE is closed fills at the last known price).
- Nasdaq and Groww are unofficial public endpoints and may change without notice. Configure Alpaca
  (US) or Kite (India) for supported feeds.
- Shorting is disabled in paper by default (`paper.allow_short`).
- The live adapters (Alpaca, Kite, CCXT) are implemented against the vendors' documented APIs and
  unit-tested for the gating logic, but they have not been exercised against a funded account from
  this environment because no credentials were available.
