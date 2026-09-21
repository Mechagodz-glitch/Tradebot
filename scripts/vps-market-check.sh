#!/usr/bin/env bash
# HALTED by the owner on 2026-09-21: all trading stopped, positions are being liquidated by hand.
# Nothing below runs. Remove these lines only to restart the system deliberately.
echo "tradebot halted by owner (2026-09-21); nothing to do"; exit 0
# Timer target: enforce thesis stops/targets/expiries on Kite, but only inside NSE hours.
# Everything else (research, new theses) happens in the cloud session or by hand.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export TZ=Asia/Kolkata
hhmm=$(date +%H%M)
# session calendar (weekdays minus exchange holidays); plus a 5-minute margin at both ends of the day
if ! ./scripts/tradebot --json hours 2>/dev/null | python3 -c "import json,sys; sys.exit(0 if any(r.get('market')=='in' and r.get('open') for r in json.load(sys.stdin)) else 1)"; then
  exit 0
fi
if [ "$hhmm" -lt 0920 ] || [ "$hhmm" -gt 1525 ]; then
  exit 0
fi
if [ -f data/KILL ]; then
  echo "kill switch active; skipping"; exit 0
fi
# no valid Kite session (the daily login has not happened yet, or the token expired): every order would be
# rejected, so skip the tick instead of recording a rejected order and pushing a snapshot every 10 minutes
if ! ./scripts/tradebot --json doctor --no-data 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(c['name']=='broker:kite' and c['ok'] for c in d['checks']) else 1)"; then
  echo "kite session not valid (no token for today yet); skipping this tick"; exit 0
fi
out="$(./scripts/tradebot --json thesis check --execute --venue kite)"
echo "$out"
./scripts/tradebot --json sync --venue kite >/dev/null || true
# when the executor actually did something (entered, exited, canceled, or failed to), push a snapshot at once so the
# cloud session sees the new thesis state within minutes instead of at the 15:45 end-of-day export
if echo "$out" | python3 -c "import json,sys; rows=json.load(sys.stdin); sys.exit(0 if any(r.get('action') for r in rows) else 1)" 2>/dev/null; then
  snap="data/snapshots/$(date +%F)-vps.json"
  ./scripts/tradebot --json export --out "$snap" >/dev/null
  git add "$snap"
  if git commit -q -m "vps snapshot $(date +%F) (intraday action at $(date +%H:%M))"; then
    git pull -q --rebase || true
    git push -q || echo "git push failed; the 15:45 export will retry"
  fi
fi
