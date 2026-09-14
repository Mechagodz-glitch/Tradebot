"""Exchange session calendars: weekday sessions plus the exchanges' published trading holidays.

Holiday lists are the official NSE/BSE and NYSE calendars for the years below; extend them each December.
A day that is not listed is assumed to be a trading day, so an unlisted ad-hoc closure is only caught
when the venue rejects the order."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Market, utcnow

SESSIONS = {
    Market.IN: ("Asia/Kolkata", time(9, 15), time(15, 30)),
    Market.US: ("America/New_York", time(9, 30), time(16, 0)),
}

HOLIDAYS: dict[Market, dict[date, str]] = {
    Market.IN: {  # NSE / BSE equity segment 2026 (Zerodha holiday calendar)
        date(2026, 1, 15): "Municipal Corporation Elections in Maharashtra", date(2026, 1, 26): "Republic Day",
        date(2026, 3, 3): "Holi", date(2026, 3, 26): "Shri Ram Navami", date(2026, 3, 31): "Shri Mahavir Jayanti",
        date(2026, 4, 3): "Good Friday", date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti", date(2026, 5, 1): "Maharashtra Day",
        date(2026, 5, 28): "Bakri Eid", date(2026, 6, 26): "Moharram", date(2026, 9, 14): "Ganesh Chaturthi",
        date(2026, 10, 2): "Mahatma Gandhi Jayanti", date(2026, 10, 20): "Dussehra", date(2026, 11, 10): "Diwali-Balipratipada",
        date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev", date(2026, 12, 25): "Christmas",
    },
    Market.US: {  # NYSE 2026
        date(2026, 1, 1): "New Year's Day", date(2026, 1, 19): "Martin Luther King Jr. Day", date(2026, 2, 16): "Presidents' Day",
        date(2026, 4, 3): "Good Friday", date(2026, 5, 25): "Memorial Day", date(2026, 6, 19): "Juneteenth",
        date(2026, 7, 3): "Independence Day (observed)", date(2026, 9, 7): "Labor Day", date(2026, 11, 26): "Thanksgiving Day",
        date(2026, 12, 25): "Christmas Day",
    },
}


def holiday_name(market: Market, day: date) -> str | None:
    return HOLIDAYS.get(market, {}).get(day)


def is_trading_day(market: Market, day: date) -> bool:
    return day.weekday() < 5 and holiday_name(market, day) is None


def market_session(market: Market, now: datetime | None = None) -> dict:
    """Return {"open": bool, "local_time": str, "next_open": iso|None, "closes_at": iso|None, "detail": str}."""
    now = now or utcnow()
    if market not in SESSIONS:
        return {"market": market.value, "open": True, "local_time": now.isoformat(), "next_open": None, "closes_at": None,
                "detail": "24x7"}
    tzname, start, end = SESSIONS[market]
    tz = ZoneInfo(tzname)
    local = now.astimezone(tz)
    today_trading = is_trading_day(market, local.date())
    open_now = today_trading and start <= local.time() < end
    if open_now:
        closes = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        return {"market": market.value, "open": True, "local_time": local.isoformat(), "next_open": None,
                "closes_at": closes.isoformat(), "detail": f"open until {end.strftime('%H:%M')} {tzname}"}
    candidate = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if local.time() >= start or not today_trading:
        candidate += timedelta(days=1)
    while not is_trading_day(market, candidate.date()):
        candidate += timedelta(days=1)
    why = holiday_name(market, local.date())
    prefix = f"holiday ({why}); " if why and local.weekday() < 5 else "closed; "
    return {"market": market.value, "open": False, "local_time": local.isoformat(), "next_open": candidate.isoformat(),
            "closes_at": None, "holiday": why, "detail": f"{prefix}next session {candidate.strftime('%a %Y-%m-%d %H:%M')} {tzname}"}


def is_open(market: Market, now: datetime | None = None) -> bool:
    return market_session(market, now)["open"]
