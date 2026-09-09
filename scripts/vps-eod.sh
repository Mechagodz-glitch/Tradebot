#!/usr/bin/env bash
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
