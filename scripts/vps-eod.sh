#!/usr/bin/env bash
# HALTED by the owner on 2026-09-21: all trading stopped, positions are being liquidated by hand.
# Nothing below runs. Remove these lines only to restart the system deliberately.
echo "tradebot halted by owner (2026-09-21); nothing to do"; exit 0
# Timer target: after the close, snapshot the day's state and push it so the cloud session can reconcile.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export TZ=Asia/Kolkata
./scripts/tradebot --json sync --venue kite >/dev/null || true
out="data/snapshots/$(date +%F)-vps.json"
./scripts/tradebot --json export --out "$out" >/dev/null
git add "$out"
if git commit -q -m "vps snapshot $(date +%F)"; then
  git pull -q --rebase || true
  git push -q || echo "git push failed; will retry tomorrow (snapshot is committed locally)"
fi
