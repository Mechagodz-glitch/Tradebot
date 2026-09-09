#!/usr/bin/env bash
# Timer target: enforce thesis stops/targets/expiries on Kite, but only inside NSE hours.
# Everything else (research, new theses) happens in the cloud session or by hand.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export TZ=Asia/Kolkata
dow=$(date +%u); hhmm=$(date +%H%M)
if [ "$dow" -gt 5 ] || [ "$hhmm" -lt 0920 ] || [ "$hhmm" -gt 1525 ]; then
  exit 0
fi
if [ -f data/KILL ]; then
  echo "kill switch active; skipping"; exit 0
fi
./scripts/tradebot --json thesis check --execute --venue kite
./scripts/tradebot --json sync --venue kite >/dev/null || true
