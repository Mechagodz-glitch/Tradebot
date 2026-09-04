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
tradebot --json thesis open NSE:X --venue kite --size 3000 --stop 5 --target 10 --expires 2026-09-16 --text "..." [--execute]
tradebot --json thesis list | thesis check [--execute] | thesis enter <id> | thesis close <id> --reason "..."
tradebot --json universe build --market in | universe show --market in --limit 30   # turnover-ranked names
tradebot --json news --match "Adani,sugar,NSE IPO" --hours 36 | news -q "Jio IPO date"      # feeds / Google News India
tradebot --json themes | themes -n adani -n sugar_ethanol --members                       # basket returns and volume
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
3. Research pass (the intelligence layer's job): news, scheduled events (index changes, IPOs, policy,
   results, regulatory), and unusual-volume names from `universe show`. Turn each view into a thesis with a
   stop, a target, an expiry and a confidence: `thesis open SYMBOL --venue kite --size N --stop S --target T
   --expires DATE --confidence C --text "..."`. Size by confidence: roughly 15% of equity at 0.5, up to 30%
   at 0.65+. Never more than `risk.max_position_notional`.
4. From about 09:30 IST: `thesis check --execute` (resolves pending entries), `thesis enter <id>` for planned
   theses, then `strategy plan --market in --venue kite`, review, `strategy run ... --execute`.
5. Every 1 to 2 hours and around 15:10 IST: `thesis check --execute` and `strategy run --execute`.
   Re-read the news between checks; a thesis whose premise broke is closed with `thesis close <id> --reason`.
6. End of day: `account`, `positions`, `thesis list`, `journal`; summarise for the human with every order's reason.

## Research pass: follow the money, not just the headlines

Run these every morning and before each intraday check. All are public information.

1. `tradebot news --match "<actors and themes>" --hours 36` over the market feeds, then `tradebot news -q "<topic>"`
   for anything that needs depth. Actors to track: Adani group, Reliance/Jio, LIC, SBI, Tata, government
   policy (Cabinet/CCEA decisions, DGFT notifications, GST council, MoRTH/NHAI, sugar and ethanol orders),
   SEBI orders, RBI actions. Look for: large capex or acquisition announcements, promoter or insider buying,
   bulk and block deals, stake sales, regulatory approvals (IPO observation letters), policy U-turns.
2. `tradebot themes` to see which baskets moved and on what volume; `tradebot themes -n <theme> --members` to
   find the lagging member of a moving basket (the better entry) and the name carrying unusual volume.
3. `tradebot universe show --limit 40` for the day's turnover leaders: unusual volume is where information is
   being acted on. Find the reason before trading it.
4. Macro map: what is stressed (crude, rupee, FII flows, rates) and who is on the right side of it.
   Crude up and rupee weak: upstream oil, tanker shipping, exporters with dollar revenue (textiles, pharma,
   IT), commodity exchanges; losers are OMCs, aviation, paints, tyres, importers.
5. Verify every premise from a primary or two independent sources before it becomes a thesis. Claims about
   politically connected businesses need documentary evidence (filings, notifications); if it cannot be
   verified, it is not a thesis. Trading on non-public information is illegal; only act on published facts.

## Discretionary theses: rules

- One thesis per catalyst. The text must name the event, the expected outcome and why the market has not
  priced it. If that cannot be written in two sentences, there is no thesis.
- Do not chase a name that moved more than ~10% on the catalyst day at many times normal volume; look for the
  lagging beneficiary instead.
- Verify the premise (e.g. the company actually holds the stake the headline claims). IFCI rallied on the NSE
  IPO story in September 2026 despite having sold its NSE stake in 2019.
- Stops are 4 to 6% for large caps and 6 to 8% for volatile mid caps; targets 1.5 to 2 times the stop.
- Expiry is the catalyst date plus a day or two, never open ended.

## Never

- Never edit `config.yaml` risk limits or `live_trading_enabled` on your own initiative.
- Never delete `data/KILL`; only `tradebot kill --off` after a human asks.
- Never send an order to a live venue without an explicit human instruction in the current session.
