"""Tradebot: a deterministic execution layer for paper and live trading.

Markets: US equities (``us``), Indian equities (``in``), crypto (``crypto``).
Venues: ``paper`` (built-in simulator), ``alpaca``, ``kite`` (Zerodha), ``ccxt``.
"""

__version__ = "0.1.0"


def _apply_network_pins() -> None:
    """TRADEBOT_FORCE_IPV4=1 makes requests/urllib3 (used by kiteconnect) connect over IPv4 only, so the
    address Zerodha sees is the IPv4 one on the app's whitelist. Also loads .env first so the flag can live there."""
    import os
    from pathlib import Path
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(os.environ.get("TRADEBOT_ROOT", ".")) / ".env", override=False)
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("TRADEBOT_FORCE_IPV4", "").lower() in ("1", "true", "yes"):
        try:
            import socket
            import urllib3.util.connection as _conn
            _conn.HAS_IPV6 = False
            _orig = socket.getaddrinfo

            def _v4_only(host, port, family=0, *args, **kwargs):
                return _orig(host, port, socket.AF_INET, *args, **kwargs)
            if not getattr(socket, "_tradebot_v4_pinned", False):
                socket.getaddrinfo = _v4_only  # type: ignore[assignment]
                socket._tradebot_v4_pinned = True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


_apply_network_pins()
