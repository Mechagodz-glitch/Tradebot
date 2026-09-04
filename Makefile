.PHONY: setup test doctor serve lint
setup:
	uv venv .venv --python 3.11 && . .venv/bin/activate && uv pip install -e ".[dev]"
test:
	. .venv/bin/activate && pytest -q
doctor:
	. .venv/bin/activate && tradebot doctor
serve:
	. .venv/bin/activate && tradebot serve
