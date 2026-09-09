"""Factor screener: the numbers Tickertape / Screener-style sites show (1y alpha and beta versus the
index, momentum, RSI, realised volatility, volume surge, distance from the 52 week high, turnover),
computed deterministically from daily candles over a whole universe, then ranked for short-horizon
trades.

Everything here is derived from price and volume only, so the same run on the same data always yields
the same list. Candles are cached per day under ``data/cache/screen/<date>/`` so a re-run with other
filters is instant; results are written to ``data/screens/<date>.json``."""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .errors import DataError
from .models import Candle, Market
from .universe import load_symbol_list, load_universe_rows

TRADING_DAYS = 252
DEFAULT_BENCHMARK = {Market.IN: "NSE:NIFTYBEES", Market.US: "SPY", Market.CRYPTO: "BTC-USD"}

# composite weights for the short-term score (percentile ranks across the passing set)
WEIGHTS = {"ret_60": 0.20, "ret_20": 0.15, "alpha": 0.15, "sharpe_60": 0.15, "rsi_fit": 0.10, "vol_ratio": 0.10,
           "near_high": 0.10, "trend": 0.05}


# ---- math helpers ---------------------------------------------------------------------------------
def returns(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


def sma(xs: list[float], n: int) -> Optional[float]:
    if len(xs) < n or n <= 0:
        return None
    return sum(xs[-n:]) / n


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def align_by_date(a: list[Candle], b: list[Candle]) -> tuple[list[float], list[float]]:
    """Closes of ``a`` and ``b`` on the dates both have a bar (holidays / missing bars differ per source)."""
    bd = {c.ts.date(): c.close for c in b}
    xa, xb = [], []
    for c in a:
        d = c.ts.date()
        if d in bd:
            xa.append(c.close)
            xb.append(bd[d])
    return xa, xb


def beta_alpha(stock_closes: list[float], bench_closes: list[float], min_obs: int = 60) -> dict:
    """OLS beta of daily returns on the benchmark, annualised Jensen alpha (%), correlation."""
    rs, rb = returns(stock_closes), returns(bench_closes)
    n = min(len(rs), len(rb))
    rs, rb = rs[-n:], rb[-n:]
    if n < min_obs:
        return {"beta": None, "alpha": None, "corr": None, "obs": n}
    ms, mb = _mean(rs), _mean(rb)
    cov = sum((x - ms) * (y - mb) for x, y in zip(rs, rb)) / (n - 1)
    var_b = sum((y - mb) ** 2 for y in rb) / (n - 1)
    sd_s = _std(rs)
    if var_b <= 0 or not sd_s:
        return {"beta": None, "alpha": None, "corr": None, "obs": n}
    beta = cov / var_b
    alpha_daily = ms - beta * mb
    corr = cov / (sd_s * math.sqrt(var_b))
    return {"beta": beta, "alpha": alpha_daily * TRADING_DAYS * 100.0, "corr": corr, "obs": n}


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):          # Wilder smoothing
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    rs_ = ag / al
    return 100.0 - 100.0 / (1.0 + rs_)


def atr_pct(candles: list[Candle], n: int = 14) -> Optional[float]:
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    atr = sum(trs[-n:]) / n
    last = candles[-1].close
    return atr / last * 100.0 if last else None


def max_drawdown_pct(closes: list[float]) -> Optional[float]:
    if not closes:
        return None
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1.0)
    return mdd * 100.0


def pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return (a / b - 1.0) * 100.0 if a is not None and b else None


# ---- per symbol metrics ----------------------------------------------------------------------------
def compute_metrics(candles: list[Candle], bench: list[Candle], last: Optional[float] = None) -> dict:
    """All factor values for one symbol. ``candles`` and ``bench`` are daily bars, oldest first."""
    closes = [c.close for c in candles]
    vols = [c.volume for c in candles]
    if last is not None and closes:
        closes = closes[:-1] + [last]
    n = len(closes)
    out: dict = {"bars": n, "last": closes[-1] if closes else None}
    if n < 30:
        out["error"] = f"only {n} bars"
        return out
    xa, xb = align_by_date(candles, bench)
    out.update(beta_alpha(xa, xb))
    r = returns(closes)
    r60 = r[-60:]
    sd60 = _std(r60)
    out.update({
        "ret_5": pct(closes[-1], closes[-6]) if n > 6 else None,
        "ret_20": pct(closes[-1], closes[-21]) if n > 21 else None,
        "ret_60": pct(closes[-1], closes[-61]) if n > 61 else None,
        "ret_120": pct(closes[-1], closes[-121]) if n > 121 else None,
        "ret_250": pct(closes[-1], closes[-251]) if n > 251 else None,
        "vol_20": (_std(r[-20:]) or 0.0) * math.sqrt(TRADING_DAYS) * 100.0 if len(r) >= 20 else None,
        "sharpe_60": (_mean(r60) / sd60 * math.sqrt(TRADING_DAYS)) if sd60 else None,
        "rsi_14": rsi(closes),
        "atr_pct": atr_pct(candles),
        "mdd_60": max_drawdown_pct(closes[-60:]),
        "high_52w": max(c.high for c in candles[-250:]),
        "low_52w": min(c.low for c in candles[-250:]),
    })
    out["dist_52w_high"] = pct(closes[-1], out["high_52w"])
    s20, s50 = sma(closes, 20), sma(closes, 50)
    s50_prev = sma(closes[:-10], 50)
    out.update({
        "sma_20": s20, "sma_50": s50,
        "dist_sma20": pct(closes[-1], s20),
        "above_sma20": bool(s20 and closes[-1] > s20),
        "sma20_gt_sma50": bool(s20 and s50 and s20 > s50),
        "sma50_rising": bool(s50 and s50_prev and s50 > s50_prev),
    })
    v20 = sum(vols[-21:-1]) / 20 if n > 21 else None
    v5 = sum(vols[-5:]) / 5 if n >= 5 else None
    out["vol_ratio"] = (v5 / v20) if v20 else None
    out["turnover_cr_20d"] = sum(c.close * c.volume for c in candles[-20:]) / 20 / 1e7 if n >= 20 else None
    return out


# ---- ranking ---------------------------------------------------------------------------------------
def _pct_rank(values: list[Optional[float]]) -> list[Optional[float]]:
    """Percentile rank (0..100) of each value among the non-null values; None stays None."""
    idx = [i for i, v in enumerate(values) if v is not None]
    order = sorted(idx, key=lambda i: values[i])
    out: list[Optional[float]] = [None] * len(values)
    m = len(order)
    for rank, i in enumerate(order):
        out[i] = 100.0 * rank / (m - 1) if m > 1 else 50.0
    return out


def passes_filters(m: dict, *, min_price: float, min_turnover_cr: float, max_momentum_pct: float,
                   max_extension_pct: float, max_rsi: float, min_bars: int) -> Optional[str]:
    """Return the first failing filter's name, or None when the row is eligible."""
    if m.get("error"):
        return "history" if m["error"].startswith("only ") else m["error"]
    if (m.get("bars") or 0) < min_bars:
        return "history"
    if m.get("beta") is None:
        return "beta"
    if (m.get("last") or 0) < min_price:
        return "price"
    if (m.get("turnover_cr") or m.get("turnover_cr_20d") or 0) < min_turnover_cr:
        return "turnover"
    if m.get("ret_20") is not None and m["ret_20"] > max_momentum_pct:
        return "parabolic"
    if m.get("dist_sma20") is not None and m["dist_sma20"] > max_extension_pct:
        return "extended"
    if m.get("rsi_14") is not None and m["rsi_14"] > max_rsi:
        return "overbought"
    return None


def score_rows(rows: list[dict], weights: Optional[dict] = None) -> list[dict]:
    """Attach ``score`` (0..100) to eligible rows; sort best first. Uninformative factors are skipped."""
    w = weights or WEIGHTS
    ok = [r for r in rows if not r.get("rejected")]
    if not ok:
        return rows
    factors = {
        "ret_60": [r.get("ret_60") for r in ok],
        "ret_20": [r.get("ret_20") for r in ok],
        "alpha": [r.get("alpha") for r in ok],
        "sharpe_60": [r.get("sharpe_60") for r in ok],
        "rsi_fit": [None if r.get("rsi_14") is None else -abs(r["rsi_14"] - 60.0) for r in ok],
        "vol_ratio": [None if r.get("vol_ratio") is None else min(r["vol_ratio"], 3.0) for r in ok],
        "near_high": [r.get("dist_52w_high") for r in ok],                     # less negative = closer = better
        "trend": [float(r.get("above_sma20", False)) + float(r.get("sma20_gt_sma50", False)) + float(r.get("sma50_rising", False))
                  for r in ok],
    }
    ranks = {k: _pct_rank(v) for k, v in factors.items()}
    for i, r in enumerate(ok):
        total, wsum = 0.0, 0.0
        for k, wk in w.items():
            v = ranks.get(k, [None])[i] if k in ranks else None
            if v is None:
                continue
            total += wk * v
            wsum += wk
        r["score"] = round(total / wsum, 1) if wsum else None
        r["beta_bucket"] = "low" if r["beta"] < 0.8 else ("high" if r["beta"] > 1.3 else "mid")
    ok.sort(key=lambda r: -(r.get("score") or 0))
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    return ok + [r for r in rows if r.get("rejected")]


# ---- data plumbing ---------------------------------------------------------------------------------
def _cache_dir(root: str, day: str) -> Path:
    return Path(root) / "data" / "cache" / "screen" / day


def _load_cached(path: Path) -> Optional[list[Candle]]:
    if not path.exists():
        return None
    try:
        return [Candle(**c) for c in json.loads(path.read_text())]
    except Exception:  # noqa: BLE001
        return None


def _save_cache(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([c.model_dump(mode="json") for c in candles]))


def _prime_kite_tokens(engine, rows: list[dict]) -> None:
    """Kite resolves instrument tokens with a quote call per symbol; the universe file already has them."""
    try:
        prov = engine.data.provider("kite")
    except Exception:  # noqa: BLE001
        return
    tokens = getattr(prov, "_tokens", None)
    if tokens is None:
        return
    for r in rows:
        if r.get("instrument_token") and r.get("symbol"):
            tokens.setdefault(r["symbol"].upper(), int(r["instrument_token"]))


def fetch_daily(engine, symbol: str, market: Market, lookback: int, cache: Optional[Path], refresh: bool = False) -> list[Candle]:
    path = cache / f"{symbol.replace(':', '_').replace('/', '_')}.json" if cache else None
    if path and not refresh:
        hit = _load_cached(path)
        if hit:
            return hit
    candles, _src = engine.candles(symbol, "1d", lookback, market)
    if path and candles:
        _save_cache(path, candles)
    return candles


def screen(engine, market: Market = Market.IN, symbols: Optional[list[str]] = None, universe_spec: Optional[str] = None,
           benchmark: Optional[str] = None, lookback: int = 260, *, min_price: float = 50.0, min_turnover_cr: float = 25.0,
           max_momentum_pct: float = 40.0, max_extension_pct: float = 12.0, max_rsi: float = 78.0, min_bars: int = 120,
           workers: int = 4, use_cache: bool = True, refresh: bool = False, save: bool = True,
           progress: Optional[Callable[[int, int, str], None]] = None, weights: Optional[dict] = None) -> dict:
    """Run the factor screen over a universe. Returns {params, benchmark, rows (ranked), rejected, saved_to}."""
    root = engine.settings.root
    spec = universe_spec or engine.settings.strategy.universe.get(market.value)
    if symbols:
        names = [s.upper() for s in symbols]
        urows: list[dict] = []
    else:
        names = load_symbol_list(spec, root)
        urows = load_universe_rows(spec, root)
    if not names:
        raise DataError(f"no symbols to screen for market {market.value}")
    meta = {r["symbol"].upper(): r for r in urows}
    _prime_kite_tokens(engine, urows)

    bench_sym = (benchmark or DEFAULT_BENCHMARK[market]).upper()
    day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    cache = _cache_dir(root, day) if use_cache else None
    bench = fetch_daily(engine, bench_sym, market, lookback, cache, refresh)
    if len(bench) < min_bars:
        raise DataError(f"benchmark {bench_sym} returned only {len(bench)} bars")

    total = len(names)
    done = [0]

    def one(sym: str) -> dict:
        row: dict = {"symbol": sym, "name": meta.get(sym, {}).get("name"), "turnover_cr": meta.get(sym, {}).get("turnover_cr")}
        try:
            candles = fetch_daily(engine, sym, market, lookback, cache, refresh)
            row.update(compute_metrics(candles, bench))
        except Exception as e:  # noqa: BLE001
            row["error"] = str(e)[:120]
        done[0] += 1
        if progress:
            progress(done[0], total, sym)
        return row

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        rows = list(ex.map(one, names))

    for r in rows:
        why = passes_filters(r, min_price=min_price, min_turnover_cr=min_turnover_cr, max_momentum_pct=max_momentum_pct,
                             max_extension_pct=max_extension_pct, max_rsi=max_rsi, min_bars=min_bars)
        if why:
            r["rejected"] = why
    ranked = score_rows(rows, weights)
    eligible = [r for r in ranked if not r.get("rejected")]
    rejected = [r for r in ranked if r.get("rejected")]
    reasons: dict[str, int] = {}
    for r in rejected:
        reasons[r["rejected"]] = reasons.get(r["rejected"], 0) + 1
    out = {
        "market": market.value, "run_at": datetime.now(timezone.utc).isoformat(), "benchmark": bench_sym, "bench_bars": len(bench),
        "params": {"lookback": lookback, "min_price": min_price, "min_turnover_cr": min_turnover_cr, "max_momentum_pct": max_momentum_pct,
                   "max_extension_pct": max_extension_pct, "max_rsi": max_rsi, "min_bars": min_bars, "weights": weights or WEIGHTS},
        "scanned": len(rows), "eligible": len(eligible), "rejected_counts": reasons, "rows": eligible,
        "rejected": [{"symbol": r["symbol"], "why": r["rejected"], "last": r.get("last"), "ret_20": r.get("ret_20")} for r in rejected],
    }
    if save:
        p = Path(root) / "data" / "screens" / f"{day}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=1, default=str))
        out["saved_to"] = str(p)
    return out
