#!/usr/bin/env bash
# One-command local setup for Tradebot (macOS / Linux / WSL).
#   git clone https://github.com/Mechagodz-glitch/Tradebot.git && cd Tradebot && ./scripts/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x .venv/bin/python ]; then
  uv venv .venv --python 3.11
fi
# shellcheck disable=SC1091
. .venv/bin/activate
uv pip install -e ".[dev]"

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "created .env from .env.example: add your ALPACA_* and KITE_* keys to it"
fi
mkdir -p data/cache

if [ -d data/snapshots ] && ls data/snapshots/*.json >/dev/null 2>&1; then
  latest=$(ls -t data/snapshots/*.json | head -1)
  echo "importing journal and theses from $latest"
  tradebot import "$latest" || true
fi

# make `tradebot` available in any shell without activating the virtualenv
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/scripts/tradebot" "$HOME/.local/bin/tradebot"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) PATH_NOTE="" ;;
  *) PATH_NOTE="  (add ~/.local/bin to PATH, e.g.: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc)" ;;
esac

echo
echo "setup complete. 'tradebot' is linked into ~/.local/bin$PATH_NOTE"
echo "next (from any directory, or use ./scripts/tradebot):"
echo "  tradebot doctor            # connectivity and credentials"
echo "  tradebot serve             # dashboard + API at http://127.0.0.1:8787"
echo "  tradebot themes            # where money is moving"
echo "  tradebot news --match 'NSE IPO,Iran,rupee'"
