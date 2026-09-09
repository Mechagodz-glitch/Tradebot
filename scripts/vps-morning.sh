#!/usr/bin/env bash
# Timer target: bring the VPS database in line with the cloud session's morning research.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
git pull -q --rebase || echo "git pull failed (continuing)"
latest=$(ls data/snapshots/*.json 2>/dev/null | sort | tail -1)   # date-named files: newest by name, not mtime
if [ -n "$latest" ]; then
  ./scripts/tradebot --json import "$latest"
fi
./scripts/tradebot --json sync --venue kite || echo "kite sync failed: is today's access token saved? (tradebot kite-login <request_token> --save)"
