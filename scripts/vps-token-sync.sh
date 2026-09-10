#!/usr/bin/env bash
# Daily Kite token hand-off, receiving side (droplet or laptop).
#
#   scripts/vps-token-sync.sh init [IDENTITY]   # one-time: publish this machine's SSH public key as a recipient,
#                                               # install pyrage, and (droplet) enable the 5-minute timer
#   scripts/vps-token-sync.sh                   # timer target: git pull, decrypt data/secrets/kite_token.age,
#                                               # update .env, restart the dashboard when the token changed
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
IDENTITY="${2:-${TRADEBOT_TOKEN_IDENTITY:-$HOME/.ssh/id_ed25519}}"
export PATH="$HOME/.local/bin:$PATH"

if [ "${1:-}" = "init" ]; then
  set -e
  [ -f "$IDENTITY.pub" ] || { echo "no public key at $IDENTITY.pub (pass the private key path as 2nd arg)"; exit 1; }
  name="$(hostname -s)"
  mkdir -p deploy/keys
  cp "$IDENTITY.pub" "deploy/keys/$name.pub"
  uv pip install -q --python .venv/bin/python -e . >/dev/null
  git add "deploy/keys/$name.pub"
  git commit -q -m "token hand-off: register recipient key $name" || true
  git pull -q --rebase && git push -q
  if command -v systemctl >/dev/null && [ -d deploy/systemd ] && [ "$(id -un)" = "trader" ]; then
    sudo cp deploy/systemd/tradebot-token.service deploy/systemd/tradebot-token.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now tradebot-token.timer
    systemctl list-timers --no-pager | grep tradebot-token || true
  fi
  echo "recipient $name registered; the next 'tradebot token drop' from the research session reaches this machine within 5 minutes"
  exit 0
fi

git pull -q --rebase 2>/dev/null || true
out="$(./scripts/tradebot --json token apply --identity "$IDENTITY" 2>&1)" || { echo "$out" | tail -2; exit 0; }
if echo "$out" | grep -q '"updated": true'; then
  echo "token updated from drop"
  if command -v systemctl >/dev/null && systemctl is-active --quiet tradebot-dashboard 2>/dev/null; then
    sudo systemctl restart tradebot-dashboard
  fi
  ./scripts/tradebot --json sync --venue kite >/dev/null 2>&1 || true
fi
