"""Exchange session calendars (weekday sessions only; exchange holidays are not modelled, the venue
rejects orders on those days and the error is surfaced as a broker_error)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Market, utcnow

SESSIONS = {
    Market.IN: ("Asia/Kolkata", time(9, 15), time(15, 30)),
    Market.US: ("America/New_York", time(9, 30), time(16, 0)),
}


def market_session(market: Market, now: datetime | None = None) -> dict:
    """Return {"open": bool, "local_time": str, "next_open": iso|None, "closes_at": iso|None}."""
    now = now or utcnow()
    if market not in SESSIONS:
        return {"market": market.value, "open": True, "local_time": now.isoformat(), "next_open": None, "closes_at": None,
                "detail": "24x7"}
    tzname, start, end = SESSIONS[market]
    tz = ZoneInfo(tzname)
    local = now.astimezone(tz)
    is_weekday = local.weekday() < 5
    open_now = is_weekday and start <= local.time() < end
    if open_now:
        closes = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        return {"market": market.value, "open": True, "local_time": local.isoformat(), "next_open": None,
                "closes_at": closes.isoformat(), "detail": f"open until {end.strftime('%H:%M')} {tzname}"}
    # find next weekday session start
    candidate = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if local.time() >= start or not is_weekday:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return {"market": market.value, "open": False, "local_time": local.isoformat(), "next_open": candidate.isoformat(),
            "closes_at": None, "detail": f"closed; next session {candidate.strftime('%a %Y-%m-%d %H:%M')} {tzname}"}


def is_open(market: Market, now: datetime | None = None) -> bool:
    return market_session(market, now)["open"]
