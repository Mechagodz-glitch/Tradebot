#!/usr/bin/env bash
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
./scripts/tradebot --json thesis check --execute --venue kite
./scripts/tradebot --json sync --venue kite >/dev/null || true
