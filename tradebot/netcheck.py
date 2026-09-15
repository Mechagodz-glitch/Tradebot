"""Which public IPv4 does this machine leave from? Zerodha accepts API orders only from the address on the
app's whitelist, so an executor must be able to prove its egress address before it is trusted with orders."""

from __future__ import annotations

import ipaddress
import time
from typing import Optional

import httpx

# plain-text echo services; the first answer that parses as an IPv4 address wins
ECHO_URLS = ("https://api.ipify.org", "https://icanhazip.com", "https://ifconfig.me/ip")


def public_ipv4(timeout: float = 4.0) -> Optional[str]:
    """The IPv4 address the internet sees for this machine, or None when no echo service answered."""
    for url in ECHO_URLS:
        try:
            r = httpx.get(url, timeout=timeout, headers={"User-Agent": "tradebot"})
            ip = r.text.strip()
            if r.status_code == 200 and isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address):
                return ip
        except Exception:  # noqa: BLE001 - try the next service
            continue
    return None


def egress_check(whitelisted: Optional[str], enforce: bool, timeout: float = 4.0) -> dict:
    """Doctor row: egress IPv4 versus the Kite whitelist. A mismatch is only a failure where orders are
    placed (live trading enabled); the research sandbox reports it as information."""
    t0 = time.perf_counter()
    ip = public_ipv4(timeout=timeout)
    ms = int((time.perf_counter() - t0) * 1000)
    if ip is None:
        return {"name": "egress:ipv4", "ok": not enforce, "detail": "unknown (no echo service reachable)", "latency_ms": ms}
    if not whitelisted:
        return {"name": "egress:ipv4", "ok": True, "detail": f"{ip}; kite.whitelisted_ip not set", "latency_ms": ms}
    if ip == whitelisted:
        return {"name": "egress:ipv4", "ok": True, "detail": f"{ip} matches the Kite whitelist", "latency_ms": ms}
    return {"name": "egress:ipv4", "ok": not enforce,
            "detail": f"{ip} is NOT the whitelisted {whitelisted}" + ("; Kite will reject orders from here" if enforce else " (not an executor)"),
            "latency_ms": ms}
