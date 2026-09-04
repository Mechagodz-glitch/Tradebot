# Operating contract for the intelligence layer

This file tells an AI agent (or any automated decision maker) how to drive Tradebot. The agent decides
**what** to trade; Tradebot decides **whether it is allowed** and **how it gets executed and recorded**.
The agent should never re-implement data fetching, order handling or bookkeeping: call the CLI or API.

## Golden rules

1. Always use `--json` and parse the output. Never scrape tables.
2. Start every session with `tradebot doctor --no-data` (fast) or `tradebot doctor` (full) and read
   `live_trading` and `kill_switch`. Refuse to act if the venue you intend to use is not `configured`.
3. Give a `--reason` on every order. It is the audit trail humans will read later.
4. Use `--dry-run` first when unsure whether an order will pass risk; the response tells you the
   notional and reference price without sending anything.
5. Treat exit code 2 with `error: risk_rejected` as final for that order. Do not retry with a smaller
   size unless the size was the stated problem (`code: order_too_large`). Never try to route around the
   kill switch or the live gate.
6. Prefer the `paper` venue unless the human has explicitly enabled live trading in `config.yaml` and
   asked for live orders in the current conversation.
7. Run `tradebot sync` before reading positions/account when resting orders exist, and at least once
   per session so the equity curve has points.

## Command cheat sheet (JSON mode)

```bash
tradebot --json doctor --no-data
tradebot --json quote BTC-USD AAPL NSE:RELIANCE
tradebot --json candles BTC-USD --interval 1h --limit 100
tradebot --json candles NSE:RELIANCE --interval 1d --limit 200
tradebot --json search "tata motors"                     # find NSE symbols
tradebot --json buy BTC-USD 0.01 --reason "..." --strategy momentum-v1
tradebot --json sell AAPL 5 --type limit --limit 330 --tif gtc --reason "..."
tradebot --json buy ETH-USD 0.5 --type stop --stop 2600 --tif gtc --reason "breakout"
tradebot --json buy NSE:INFY 10 --dry-run
tradebot --json orders --open
tradebot --json order <id>
tradebot --json cancel <id>          |  tradebot --json cancel --all --symbol AAPL
tradebot --json close BTC-USD --reason "target hit"
tradebot --json positions            |  tradebot --json account --market crypto
tradebot --json sync
tradebot --json journal --limit 50   |  tradebot --json note "thesis: ..." --symbol AAPL
tradebot --json kill                 |  tradebot --json kill --off
tradebot --json hours                                    # session open/closed per market
tradebot --json strategy plan --market in --venue kite   # signals + proposed orders, sends nothing
tradebot --json strategy run --market in --venue kite    # risk-checked dry run
tradebot --json strategy run --market in --venue kite --execute   # places the plan
```

Equivalent HTTP calls exist for every command (see README). Use the API when running the agent as a
long-lived process; use the CLI for one-shot actions.

## Symbols

- Crypto: `BASE-QUOTE`, e.g. `BTC-USD`, `ETH-USD`, `SOL-USDT`. `BTC/USD` is accepted and normalised.
- US equities: ticker only, e.g. `AAPL`, `BRK.B`.
- Indian equities: `NSE:SYMBOL` (or `BSE:`). Use `search` to resolve names to symbols.
- Market is inferred from the form. Pass `--market` only to disambiguate (e.g. `--market in INFY`).

## Reading order results

`status` is one of `new accepted partially_filled filled canceled rejected expired`.

- `filled`: done. `avg_fill_price`, `filled_qty`, `fees` are populated.
- `accepted`: resting (limit/stop not marketable). It fills on a later `sync` (paper) or at the venue.
- `rejected`: `reject_reason` says why (`insufficient_funds`, `insufficient_position`, venue message).
- A risk rejection never creates an order; the CLI exits 2 with `{"error": "risk_rejected", "code": ...}`.

## Risk limits the agent must respect (defaults from config.yaml)

| Limit | USD | INR |
|---|---|---|
| Max order notional | 5,000 | 4,000 |
| Max resulting position notional | 20,000 | 4,500 |
| Max daily loss before only reducing orders are allowed | 1,000 | 400 |

Plus at most 20 open orders and 10 orders per minute per venue. Live Indian orders are also limited to the
symbols in `risk.allowed_symbols.in` and to the NSE session (09:15 to 15:30 IST, weekdays). Size orders accordingly before sending;
`--dry-run` confirms.

## Suggested loop for a strategy session

1. `doctor --no-data` -> abort if kill switch on or venue unavailable.
2. `account` and `positions` -> know cash and exposure.
3. `candles` / `quote` for the watchlist -> compute signals in the intelligence layer.
4. `buy`/`sell` with `--reason` and `--strategy`; `--dry-run` if size is near a limit.
5. `sync`, then `positions` to confirm state.
6. `note` a short summary of what was done and why.

## Live day routine (Zerodha)

1. Ask the human for the Kite request token; `kite-login <token> --save`; `doctor --no-data`.
2. `account --venue kite` to confirm funds. Do not proceed if funds are missing.
3. From about 09:30 IST: `strategy plan --market in --venue kite`, review, then `strategy run ... --execute`.
4. Repeat `strategy run --execute` every 1 to 2 hours and once around 15:10 IST for stops and trend breaks.
5. End of day: `account`, `positions`, `journal`; summarise for the human.

## Never

- Never edit `config.yaml` risk limits or `live_trading_enabled` on your own initiative.
- Never delete `data/KILL`; only `tradebot kill --off` after a human asks.
- Never send an order to a live venue without an explicit human instruction in the current session.
