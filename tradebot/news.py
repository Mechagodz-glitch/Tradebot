"""News retrieval below the intelligence layer: RSS feeds that are reachable without credentials.

``search_news`` queries Google News (India edition) for a phrase; ``scan_headlines`` aggregates the
market feeds (Economic Times, Business Standard, Mint, Zerodha Pulse, Google Business) and filters by
keywords. Output is a plain list of {title, link, source, published} sorted newest first."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus

from .data.base import http_client

FEEDS = {
    "google_business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en",
    "et_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "bs_markets": "https://www.business-standard.com/rss/markets-106.rss",
    "mint_markets": "https://www.livemint.com/rss/markets",
    "pulse": "https://pulse.zerodha.com/feed.php",
}
GOOGLE_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def parse_rss(xml_text: str, source: str) -> list[dict]:
    """Parse RSS 2.0 (and tolerate Atom-ish feeds). Never raises on a single bad item."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = _parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        src_el = it.find("source")
        src = src_el.text.strip() if src_el is not None and src_el.text else source
        if title:
            items.append({"title": re.sub(r"\s+", " ", title), "link": link, "source": src, "published": pub.isoformat() if pub else None,
                          "_ts": pub or datetime(1970, 1, 1, tzinfo=timezone.utc)})
    return items


def _fetch(url: str, timeout: float = 20.0) -> str:
    with http_client(timeout=timeout) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def search_news(query: str, hours: int = 48, limit: int = 40) -> list[dict]:
    when = f" when:{max(1, hours // 24)}d" if hours >= 24 else " when:1d"
    url = GOOGLE_SEARCH.format(q=quote_plus(query + when))
    items = parse_rss(_fetch(url), "google")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = [i for i in items if i["_ts"] >= cutoff or i["published"] is None]
    items.sort(key=lambda i: i["_ts"], reverse=True)
    return [{k: v for k, v in i.items() if k != "_ts"} for i in items[:limit]]


def scan_headlines(match: Optional[list[str]] = None, feeds: Optional[list[str]] = None, hours: int = 36, limit: int = 60) -> list[dict]:
    names = feeds or list(FEEDS)
    out: list[dict] = []
    errors: list[str] = []
    for name in names:
        url = FEEDS.get(name)
        if not url:
            errors.append(f"unknown feed {name}")
            continue
        try:
            out.extend(parse_rss(_fetch(url), name))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = [i for i in out if i["published"] is None or i["_ts"] >= cutoff]
    if match:
        kws = [k.lower() for k in match if k]
        out = [i for i in out if any(k in i["title"].lower() for k in kws)]
    seen, dedup = set(), []
    for i in sorted(out, key=lambda i: i["_ts"], reverse=True):
        key = i["title"].lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        dedup.append({k: v for k, v in i.items() if k != "_ts"})
    if errors:
        dedup.append({"title": "feed errors: " + "; ".join(errors), "link": "", "source": "tradebot", "published": None})
    return dedup[:limit]
