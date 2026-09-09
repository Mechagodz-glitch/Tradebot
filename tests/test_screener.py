from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.models import Candle, Market
from tradebot.screener import (align_by_date, beta_alpha, compute_metrics, passes_filters, rsi, score_rows, screen)


def _lcg(seed: int):
    x = seed
    while True:
        x = (1103515245 * x + 12345) % (2 ** 31)
        yield x / (2 ** 31) - 0.5


def _series(n: int, start: float, rets: list[float]) -> list[float]:
    out = [start]
    for r in rets[: n - 1]:
        out.append(out[-1] * (1 + r))
    return out


def _candles(closes: list[float], vol: float = 1_000_000.0, day0: datetime | None = None) -> list[Candle]:
    day0 = day0 or datetime(2025, 9, 1, tzinfo=timezone.utc)
    return [Candle(ts=day0 + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99, close=c, volume=vol) for i, c in enumerate(closes)]


def test_beta_alpha_recovers_synthetic_exposure():
    g = _lcg(7)
    rb = [next(g) * 0.02 for _ in range(300)]
    rs = [1.5 * r + 0.001 for r in rb]                     # beta 1.5, 0.1% a day of alpha
    bench = _series(301, 100.0, rb)
    stock = _series(301, 50.0, rs)
    m = beta_alpha(stock, bench)
    assert abs(m["beta"] - 1.5) < 0.02
    assert abs(m["alpha"] - 0.001 * 252 * 100) < 0.5
    assert m["corr"] > 0.999 and m["obs"] == 300
    assert beta_alpha(stock[:30], bench[:30])["beta"] is None  # too few observations


def test_rsi_direction_and_bounds():
    up = [100 + i for i in range(40)]
    down = [140 - i for i in range(40)]
    assert rsi(up) == 100.0
    assert rsi(down) < 30.0
    assert rsi(up[:10]) is None
    wobble = [100 + (1 if i % 2 else -1) for i in range(60)]
    assert 40 < rsi(wobble) < 60


def test_align_by_date_uses_common_sessions():
    a = _candles([1, 2, 3, 4])
    b = _candles([10, 20, 30], day0=datetime(2025, 9, 2, tzinfo=timezone.utc))
    xa, xb = align_by_date(a, b)
    assert xa == [2, 3, 4] and xb == [10, 20, 30]


def test_compute_metrics_and_filters():
    g = _lcg(3)
    rb = [next(g) * 0.02 for _ in range(260)]
    bench = _candles(_series(261, 100.0, rb))
    trending = _candles(_series(261, 100.0, [r * 0.8 + 0.0005 for r in rb]))
    m = compute_metrics(trending, bench)
    for k in ("beta", "alpha", "ret_20", "ret_60", "rsi_14", "vol_ratio", "dist_52w_high", "sma_20", "sma_50", "atr_pct", "turnover_cr_20d"):
        assert m.get(k) is not None, k
    assert m["above_sma20"] and m["sma20_gt_sma50"] and m["sma50_rising"]
    assert passes_filters(m, min_price=50, min_turnover_cr=0, max_momentum_pct=40, max_extension_pct=12, max_rsi=78, min_bars=120) is None
    assert passes_filters(m, min_price=1e9, min_turnover_cr=0, max_momentum_pct=40, max_extension_pct=12, max_rsi=78, min_bars=120) == "price"
    assert passes_filters(m, min_price=50, min_turnover_cr=1e9, max_momentum_pct=40, max_extension_pct=12, max_rsi=78, min_bars=120) == "turnover"
    flat = compute_metrics(_candles([100.0] * 261), bench)
    assert flat["beta"] is None                             # zero variance: no exposure estimate
    assert passes_filters(flat, min_price=50, min_turnover_cr=0, max_momentum_pct=40, max_extension_pct=12, max_rsi=78, min_bars=120) == "beta"
    parabolic = compute_metrics(_candles(_series(261, 100.0, [0.0] * 240 + [0.03] * 20)), bench)
    assert passes_filters(parabolic, min_price=50, min_turnover_cr=0, max_momentum_pct=40, max_extension_pct=12, max_rsi=78, min_bars=120) in (
        "parabolic", "extended", "overbought")


def test_score_rows_ranks_and_skips_rejected():
    rows = [
        {"symbol": "A", "beta": 1.0, "ret_60": 10, "ret_20": 5, "alpha": 20, "sharpe_60": 2, "rsi_14": 60, "vol_ratio": 1.5, "dist_52w_high": -2,
         "above_sma20": True, "sma20_gt_sma50": True, "sma50_rising": True},
        {"symbol": "B", "beta": 0.5, "ret_60": -5, "ret_20": -3, "alpha": -10, "sharpe_60": -1, "rsi_14": 35, "vol_ratio": 0.7, "dist_52w_high": -30,
         "above_sma20": False, "sma20_gt_sma50": False, "sma50_rising": False},
        {"symbol": "C", "beta": 1.6, "rejected": "parabolic"},
    ]
    out = score_rows(rows)
    assert [r["symbol"] for r in out] == ["A", "B", "C"]
    assert out[0]["score"] > out[1]["score"] and out[0]["rank"] == 1
    assert out[0]["beta_bucket"] == "mid" and out[1]["beta_bucket"] == "low"
    assert "score" not in out[2]


def test_screen_end_to_end_with_fake_provider(engine, tmp_path, prices):
    g = _lcg(11)
    rb = [next(g) * 0.02 for _ in range(260)]
    series = {
        "NSE:NIFTYBEES": _series(261, 250.0, rb),
        "NSE:RELIANCE": _series(261, 1300.0, [r * 1.1 + 0.001 for r in rb]),
        "NSE:INFY": _series(261, 1500.0, [r * 0.9 - 0.001 for r in rb]),
        "NSE:FLAT": [200.0] * 261,
    }
    fake = engine.data.fake

    def candles(inst, interval="1d", limit=100, start=None, end=None):
        if inst.symbol not in series:
            raise KeyError(inst.symbol)
        return _candles(series[inst.symbol][-limit:], vol=2_000_000.0)

    fake.candles = candles
    res = screen(engine, Market.IN, symbols=["NSE:RELIANCE", "NSE:INFY", "NSE:FLAT", "NSE:MISSING"], lookback=261, min_turnover_cr=0,
                 workers=2, use_cache=True)
    assert res["scanned"] == 4 and res["eligible"] == 2
    assert res["rows"][0]["symbol"] == "NSE:RELIANCE" and res["rows"][0]["rank"] == 1
    assert res["rows"][0]["beta"] > res["rows"][1]["beta"]
    assert res["rejected_counts"].get("beta") == 1                    # NSE:FLAT
    assert any(r["symbol"] == "NSE:MISSING" for r in res["rejected"])
    saved = Path(res["saved_to"])
    assert saved.exists() and json.loads(saved.read_text())["eligible"] == 2
    # candles are cached for the day, so a second run does not need the provider
    fake.candles = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    again = screen(engine, Market.IN, symbols=["NSE:RELIANCE", "NSE:INFY"], lookback=261, min_turnover_cr=0, save=False)
    assert again["eligible"] == 2 and "saved_to" not in again
