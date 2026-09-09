# Handover: running the live book from your machine

Zerodha requires API orders to come from a whitelisted static IP (exchange rule, effective
1 April 2026; at most two IPs, one change per calendar week). The cloud session's IP rotates, so
**your machine places orders**. The cloud session (Claude) does research, maintains the theses and
monitors positions read-only. Only one place ever has `live_trading_enabled` on: yours.

## 0. One-time setup (10 minutes)

```bash
cd ~/personal/Tradebot && git pull && ./scripts/bootstrap.sh
nano .env          # ALPACA_API_KEY, ALPACA_SECRET_KEY, KITE_API_KEY, KITE_API_SECRET
```

Whitelist your **IPv4** address at https://developers.kite.trade -> Profile (top right) -> IP Whitelist:

```bash
curl -4 -s https://ifconfig.me   # the -4 matters: this is the IPv4 address Zerodha will see
```

Do not whitelist an IPv6 address (the long one with colons). Kite's API answers on both protocols,
and an IPv6 address from an Indian ISP changes often (the suffix is a rotating privacy address and
the prefix changes on reconnect). Tradebot therefore pins its Kite traffic to IPv4:

```bash
echo "TRADEBOT_FORCE_IPV4=1" >> .env
echo "TRADEBOT_LIVE=1" >> .env         # live trading on this machine only
tradebot doctor --no-data              # network: IPv4 only; live_trading: ENABLED
```

Enter the IPv4 address as the primary IP and click Update. Use a connection whose address is
stable: an office static IP is best; home broadband usually keeps its address for days or weeks but
can change on a router reboot. Only one whitelist change per week is allowed, so do not spend it on a
phone hotspot. If your router's WAN address starts with 100.64 to 100.127 the ISP shares the public
address (CGNAT) and it may change without warning; a static IP from the ISP or a VPS is the fix.
Read-only calls work from any IP; only order placement is restricted.

## 1. Every trading morning (09:00 to 09:15 IST)

```bash
git pull && tradebot import data/snapshots/$(ls data/snapshots | tail -1)   # research + theses from the cloud session
tradebot kite-login                         # open the URL, log in with TOTP
tradebot kite-login <request_token> --save  # request_token from the redirect URL; single use, valid minutes
tradebot doctor --no-data                   # broker:kite ok, session:in open from 09:15
tradebot account --venue kite               # funds
tradebot thesis list                        # what is planned for today, with size, stop, target, expiry, confidence
```

Read the thesis text: it names the catalyst and the conditions (for example "skip if it gaps more
than 3% above Friday's close"). If a premise has broken, cancel it: `tradebot thesis close <id> --reason "..."`.

## 2. Entries (from about 09:30 IST, after the opening volatility)

```bash
tradebot quote NSE:SWIGGY NSE:GICRE NSE:OIL   # check the gap rule against prev_close first
tradebot thesis enter <id>                    # one per planned thesis; marketable limit at ask + 15 bps, DAY, CNC
tradebot strategy plan --market in --venue kite       # optional systematic sleeve: review the eligible names
tradebot strategy run --market in --venue kite --execute   # places it with the remaining cash (max 2 slots)
```

Every order passes the risk engine: symbol whitelist (552 liquid names), NSE session hours, at most
4,000 INR per order and 4,500 INR per name, 400 INR daily loss cap, open-order and rate limits.
A rejection prints the reason and code; do not work around it.

## 3. During the day (about 11:30, 13:30 and 15:10 IST)

```bash
tradebot thesis check --execute     # closes any thesis whose stop, target or expiry is hit (market order)
tradebot strategy run --market in --venue kite --execute   # trend breaks / stops for the systematic sleeve
tradebot positions --venue kite && tradebot account --venue kite
```

The cloud session runs the same checks read-only at those times and messages you if something needs
attention, with the exact command.

## 4. End of day (after 15:30 IST)

```bash
tradebot sync --venue kite
tradebot export --out data/snapshots/$(date +%F)-laptop.json
git add data/snapshots && git commit -m "laptop snapshot $(date +%F)" && git push
```

The `-laptop` suffix matters: the cloud session writes `data/snapshots/<date>.json`, so a plain `tradebot export`
would create a conflicting file with the same name. If `git pull` ever reports divergent branches, run
`git config pull.rebase true` once and pull again.

The snapshot carries your fills, orders and thesis states back to the cloud session, which imports
it before the next morning's research. If you forget, the cloud session can still read positions
directly from Zerodha and attach them: `tradebot thesis attach <id>` (no arguments) reads the venue
position's quantity and average price.

## 5. Emergency controls

```bash
tradebot kill                 # every new order is rejected until: tradebot kill --off
tradebot cancel --all --venue kite
tradebot close NSE:XXX --venue kite --reason "..."   # flatten one position at market
```

The Kite app itself always works as a manual override; the tool reads whatever you do there.

## 6. Today, Monday 7 September 2026

Three planned theses, all premises verified this morning (Brent ~97 after weekend US-Iran tanker
strikes; NSE IPO price band due ~11 Sep; Swiggy MSCI deletion executed into Friday's close):

| Thesis id | Symbol | Size | Stop | Target | Expires | Note |
|---|---|---|---|---|---|---|
| 90f5f2db97a0 | NSE:SWIGGY | 2,500 | 5% | 10% | 18 Sep | post-deletion rebound |
| 849f600df9e3 | NSE:GICRE | 2,000 | 5% | 10% | 16 Sep | NSE IPO selling shareholder; skip if it gaps >3% above 358.45 |
| 9bfaa74c2640 | NSE:OIL | 2,400 | 5% | 8% | 18 Sep | crude beneficiary; cancel if a ceasefire drops Brent under 90 |

At 10:29 IST prices the entries would be about 8 Swiggy @ 279, 5 GIC Re @ 353, 4 Oil India @ 487.
`tradebot thesis enter <id>` recomputes quantity and limit from the live ask when you run it.
Entries after about 14:00 IST are not worth making; roll to Tuesday instead.

## Division of labour

| Cloud session (Claude) | Your machine |
|---|---|
| Morning research: news, themes, factor screen (`tradebot screen`), unusual volume, macro | Kite login, funds check |
| Writes/updates theses and journal, pushes snapshot | Pulls snapshot, enters theses, runs the strategy sleeve |
| Read-only monitoring at 11:30, 13:30, 15:10; alerts you | Runs `thesis check --execute` at those times |
| End-of-day summary and next-day plan | Exports and pushes the day's snapshot |

The permanent fix is a small always-on machine with a static IP (any cloud VM in Mumbai) running
`tradebot serve` behind a bearer token; the cloud session can then drive it over HTTP and the daily
login becomes the only manual step. Until then this split is the process.
