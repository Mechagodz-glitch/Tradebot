"""Liquidity-screened tradeable universes.

``build_in_universe`` takes every NSE cash-equity instrument from the Upstox master, batch-quotes
them through Kite (500 per call), and keeps names with enough daily turnover and an affordable price.
The result is written to ``data/universe/in.json`` and can be referenced from config as
``file:data/universe/in.json`` (risk.allowed_symbols and strategy.universe both accept file refs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import DataError
from .models import Market


def load_symbol_list(spec, root: str = ".") -> list[str]:
    """A list stays a list; ``file:<path>`` loads a JSON {"symbols": [...]} or newline text file."""
    if spec is None:
        return []
    if isinstance(spec, str):
        if not spec.startswith("file:"):
            return [spec]
        path = Path(spec[5:])
        if not path.is_absolute():
            path = Path(root) / path
        if not path.exists():
            raise DataError(f"symbol list file not found: {path}")
        text = path.read_text()
        if path.suffix == ".json":
            data = json.loads(text)
            return [s.upper() for s in (data["symbols"] if isinstance(data, dict) else data)]
        return [ln.strip().upper() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return [s.upper() for s in spec]


def load_universe_rows(spec, root: str = ".") -> list[dict]:
    """Rows (symbol, turnover_cr, ...) when ``spec`` is a ``file:`` JSON universe; otherwise empty."""
    if not (isinstance(spec, str) and spec.startswith("file:")):
        return []
    path = Path(spec[5:])
    if not path.is_absolute():
        path = Path(root) / path
    if not path.exists() or path.suffix != ".json":
        return []
    data = json.loads(path.read_text())
    return data.get("rows", []) if isinstance(data, dict) else []


def build_in_universe(engine, min_turnover_cr: float = 25.0, max_price: Optional[float] = None,
                      out_path: str = "data/universe/in.json") -> dict:
    """Screen all NSE equities by Friday's turnover (average price x volume, in crore INR)."""
    upstox = engine.data.provider("upstox")
    table = upstox._load_instruments("NSE")
    names = [k for k, r in table.items() if r.get("instrument_type") == "EQ" and r.get("segment") == "NSE_EQ"
             and r.get("security_type", "NORMAL") == "NORMAL"]
    kite = engine.data.provider("kite").kite
    rows = []
    keys = [f"NSE:{n}" for n in names]
    for i in range(0, len(keys), 500):
        batch = keys[i:i + 500]
        try:
            q = kite.quote(batch)
        except Exception as e:  # noqa: BLE001
            raise DataError(f"kite quote batch failed: {e}") from e
        for key, d in q.items():
            vol = float(d.get("volume") or 0)
            px = float(d.get("average_price") or d.get("last_price") or 0)
            if px <= 0 or vol <= 0:
                continue
            turnover_cr = px * vol / 1e7
            rows.append({"symbol": key, "name": table.get(key.split(":", 1)[1], {}).get("name"), "last": float(d.get("last_price") or 0),
                         "volume": vol, "turnover_cr": round(turnover_cr, 2), "instrument_token": d.get("instrument_token")})
    keep = [r for r in rows if r["turnover_cr"] >= min_turnover_cr and (max_price is None or r["last"] <= max_price)]
    keep.sort(key=lambda r: -r["turnover_cr"])
    out = {"market": Market.IN.value, "built_at": datetime.now(timezone.utc).isoformat(), "min_turnover_cr": min_turnover_cr,
           "max_price": max_price, "scanned": len(rows), "count": len(keep), "symbols": [r["symbol"] for r in keep], "rows": keep}
    p = Path(engine.settings.root) / out_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    return out
