"""Theme baskets: actors (Adani, Reliance...), policy themes (sugar/ethanol, toll roads) and macro
beneficiaries (exporters, upstream oil, gold). ``theme_report`` shows where money is moving:
1d/5d/20d returns and the latest day's volume versus its 20 day average, per member and per basket.

Override or extend with a ``themes.yaml`` at the project root ({theme: [symbols]})."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .models import Market

DEFAULT_THEMES: dict[str, list[str]] = {
    "adani": ["NSE:ADANIENT", "NSE:ADANIPORTS", "NSE:ADANIGREEN", "NSE:ADANIPOWER", "NSE:ADANIENSOL", "NSE:ATGL", "NSE:AWL",
              "NSE:AMBUJACEM", "NSE:ACC"],
    "reliance": ["NSE:RELIANCE", "NSE:JIOFIN", "NSE:NETWORK18"],
    "sugar_ethanol": ["NSE:BALRAMCHIN", "NSE:TRIVENI", "NSE:DALMIASUG", "NSE:DHAMPURSUG", "NSE:RENUKA", "NSE:BAJAJHIND", "NSE:EIDPARRY",
                      "NSE:PRAJIND", "NSE:GULPOLY", "NSE:GLOBUSSPR", "NSE:BCLIND", "NSE:DWARKESH", "NSE:AVADHSUGAR", "NSE:UTTAMSUGAR"],
    "toll_roads_infra": ["NSE:IRB", "NSE:IRBINVIT", "NSE:CUBEINVIT", "NSE:HGINFRA", "NSE:KNRCON", "NSE:PNCINFRA", "NSE:DBL", "NSE:ASHOKA",
                         "NSE:GRINFRA"],
    "exporters_textile": ["NSE:WELSPUNLIV", "NSE:TRIDENT", "NSE:GOKEX", "NSE:KPRMILL", "NSE:ARVIND", "NSE:VTL", "NSE:INDOCOUNT"],
    "pharma_export": ["NSE:SUNPHARMA", "NSE:DRREDDY", "NSE:CIPLA", "NSE:LUPIN", "NSE:AUROPHARMA", "NSE:DIVISLAB", "NSE:SHILPAMED",
                      "NSE:LAURUSLABS", "NSE:GLENMARK", "NSE:NATCOPHARM", "NSE:ZYDUSLIFE"],
    "it_export": ["NSE:TCS", "NSE:INFY", "NSE:HCLTECH", "NSE:WIPRO", "NSE:TECHM", "NSE:LTIM", "NSE:PERSISTENT", "NSE:COFORGE", "NSE:MPHASIS"],
    "upstream_oil": ["NSE:ONGC", "NSE:OIL"],
    "shipping_tankers": ["NSE:GESHIP", "NSE:SCI", "NSE:SEAMEC"],
    # politically connected ethanol / flex-fuel chain (CIAN Agro: promoter Nikhil Gadkari; Manas Agro is its subsidiary)
    "ethanol_flexfuel": ["BSE:CIANAGRO", "NSE:PRAJIND", "NSE:GULPOLY", "NSE:GLOBUSSPR", "NSE:BCLIND", "NSE:TRIVENI", "NSE:BALRAMCHIN",
                         "NSE:DALMIASUG", "NSE:MARUTI", "NSE:HEROMOTOCO"],
    "crude_importers_losers": ["NSE:HINDPETRO", "NSE:BPCL", "NSE:IOC", "NSE:INDIGO", "NSE:ASIANPAINT", "NSE:BERGEPAINT", "NSE:PIDILITIND",
                               "NSE:APOLLOTYRE"],
    "gold_silver": ["NSE:GOLDBEES", "NSE:SILVERBEES", "NSE:HINDZINC"],
    "exchanges_capital_markets": ["NSE:MCX", "NSE:BSE", "NSE:CDSL", "NSE:ANGELONE", "NSE:HDFCAMC", "NSE:NAM-INDIA", "NSE:CAMS", "NSE:KFINTECH",
                                  "NSE:MOTILALOFS", "NSE:NUVAMA", "NSE:360ONE"],
    "nse_ipo_stakeholders": ["NSE:LICI", "NSE:SBIN", "NSE:GICRE", "NSE:NIACL", "NSE:BANKBARODA"],
    "defence_psu": ["NSE:HAL", "NSE:BEL", "NSE:BDL", "NSE:MAZDOCK", "NSE:COCHINSHIP", "NSE:BEML", "NSE:GRSE"],
    "psu_banks": ["NSE:SBIN", "NSE:BANKBARODA", "NSE:PNB", "NSE:CANBK", "NSE:UNIONBANK", "NSE:INDIANB", "NSE:BANKINDIA"],
}


def load_themes(root: str = ".") -> dict[str, list[str]]:
    themes = {k: list(v) for k, v in DEFAULT_THEMES.items()}
    p = Path(root) / "themes.yaml"
    if p.exists():
        extra = yaml.safe_load(p.read_text()) or {}
        for k, v in extra.items():
            themes[k] = [s.upper() for s in v]
    return themes


def theme_report(engine, names: Optional[list[str]] = None, market: Market = Market.IN, lookback: int = 25) -> list[dict]:
    themes = load_themes(engine.settings.root)
    names = names or list(themes)
    out: list[dict] = []
    for name in names:
        members = themes.get(name)
        if not members:
            out.append({"theme": name, "error": "unknown theme", "members": []})
            continue
        insts = [engine.instrument(s, market) for s in members]
        try:
            quotes = engine.data.quote_many(insts)
        except Exception:  # noqa: BLE001
            quotes = {}
        rows = []
        for s in members:
            row: dict = {"symbol": s}
            try:
                candles, src = engine.candles(s, "1d", lookback, market)
                closes = [c.close for c in candles]
                vols = [c.volume for c in candles]
                q = quotes.get(s)
                last = q.last if q else closes[-1]
                # if the live quote is from the same session as the last candle, compare against the prior close
                ref_prev = closes[-2] if (q and q.prev_close is None) else (q.prev_close if q else closes[-2])
                row.update({
                    "last": last,
                    "d1": (last / ref_prev - 1) * 100 if ref_prev else None,
                    "d5": (last / closes[-6] - 1) * 100 if len(closes) > 6 else None,
                    "d20": (last / closes[-21] - 1) * 100 if len(closes) > 21 else None,
                    "vol_ratio": (vols[-1] / (sum(vols[-21:-1]) / 20)) if len(vols) > 21 and sum(vols[-21:-1]) > 0 else None,
                    "source": src,
                })
            except Exception as e:  # noqa: BLE001
                row["error"] = str(e)[:80]
            rows.append(row)
        ok = [r for r in rows if "error" not in r]
        def avg(key):
            vals = [r[key] for r in ok if r.get(key) is not None]
            return sum(vals) / len(vals) if vals else None
        out.append({"theme": name, "members": rows, "n": len(ok), "avg_d1": avg("d1"), "avg_d5": avg("d5"), "avg_d20": avg("d20"),
                    "max_vol_ratio": max([r["vol_ratio"] for r in ok if r.get("vol_ratio")] or [None]) if ok else None})
    return out
