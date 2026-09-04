#!/bin/bash
# SessionStart hook: make a fresh Claude Code on the web container ready to run tradebot.
# Idempotent: creates .venv if missing and installs the package + dev deps.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x .venv/bin/python ]; then
  uv venv .venv --python 3.11 -q
fi
. .venv/bin/activate
uv pip install -q -e ".[dev]"

mkdir -p data/cache
echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE:-/dev/null}"
echo "tradebot session hook: venv ready ($(python --version))"
