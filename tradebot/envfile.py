"""Minimal dotenv writer shared by the CLI and the token hand-off."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def upsert_env(path, key: str, value: str) -> None:
    """Set KEY=value in a dotenv file, replacing an existing line or appending. Mode 600."""
    p = Path(path)
    lines = p.read_text().splitlines() if p.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n")
    try:
        p.chmod(0o600)
    except OSError:
        pass


def read_env(path, key: str) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    for ln in p.read_text().splitlines():
        if ln.startswith(f"{key}="):
            return ln.split("=", 1)[1].strip().split(" #", 1)[0].strip() or None
    return None
