import numpy as np
import pandas as pd
import pytest

from bot.risk.sizing import lots_for_risk, value_matched_pair
from bot.strategies import gold_silver_ratio as s3
from bot.strategies import indicators as ind
from bot.strategies import trend as s1


def _bars(close, spread=1.0):
    c = pd.Series(np.asarray(close, dtype=float), index=pd.bdate_range("2020-01-01", periods=len(close)))
    return pd.DataFrame({"open": c, "high": c + spread, "low": c - spread, "close": c})


# ---- indicators -----------------------------------------------------------

def test_ema_matches_definition():
    s = pd.Series(np.arange(1, 11, dtype=float))
    e = ind.ema(s, 3)
    k = 2 / 4
    manual = [s[0]]
    for x in s[1:]:
        manual.append(manual[-1] + k * (x - manual[-1]))
    assert np.allclose(e.iloc[2:], manual[2:]) and e.iloc[:2].isna().all()


def test_atr_constant_range():
    b = _bars([100] * 60, spread=2.0)
    assert ind.atr(b["high"], b["low"], b["close"], 14).iloc[-1] == pytest.approx(4.0)


def test_prior_high_excludes_today():
    h = pd.Series([1, 2, 3, 10.0])
    assert ind.prior_high(h, 3).iloc[-1] == 3        # today's 10 is not included


def test_mean_reversion_stats():
    rng = np.random.default_rng(3)
    ar = [0.0]
    for _ in range(600):
        ar.append(0.8 * ar[-1] + rng.normal(0, 1))
    t, hl = ind.rolling_mean_reversion(pd.Series(ar), 500)
    assert t.iloc[-1] < -4                                  # clearly mean-reverting
    assert hl.iloc[-1] == pytest.approx(-np.log(2) / np.log(0.8), rel=0.35)
    walk = pd.Series(np.cumsum(rng.normal(0, 1, 600)))
    t2, _ = ind.rolling_mean_reversion(walk, 500)
    assert t2.iloc[-1] > -2.86                              # random walk is not


def test_sanitize_ohlc():
    df = pd.DataFrame({"open": [10.0], "high": [9.0], "low": [11.0], "close": [12.0]})
    out = ind.sanitize_ohlc(df)
    assert (out["high"].iloc[0], out["low"].iloc[0]) == (12.0, 9.0)


# ---- sizing -----------------------------------------------------------------

@pytest.mark.parametrize("equity,risk,dist,mult,lots", [
    (200000, 0.5, 41, 10, 2),       # Rs 1,000 budget / Rs 410 per lot -> 2
    (200000, 0.5, 101, 10, 0),      # one lot over budget -> skip
    (200000, 1.0, 100, 10, 2),      # exactly on budget
    (200000, 0.5, 0, 10, 0),
])
def test_lots_for_risk(equity, risk, dist, mult, lots):
    assert lots_for_risk(equity, risk, dist, mult) == lots


def test_value_matched_pair():
    p = value_matched_pair(30000, 12500, 10000)
    assert p is not None and p.long_value <= 30000 and p.short_value <= 30000 and p.mismatch <= 0.25
    assert value_matched_pair(10000, 12500, 10000) is None   # one gold lot already over target


# ---- S1 rules -----------------------------------------------------------------

def test_trend_long_entry_and_no_lookahead():
    close = list(np.linspace(100, 200, 260))
    sig = s1.prepare(_bars(close, spread=0.1))      # rises faster than the bar range
    assert s1.entry_signal(sig.iloc[-1]) == 1
    # the same row computed with data only up to that day gives the same answer
    sig_short = s1.prepare(_bars(close[:-5], spread=0.1))
    pd.testing.assert_series_equal(sig.iloc[len(close) - 6], sig_short.iloc[-1], check_names=False)


def test_trend_needs_ema_alignment():
    # long downtrend then a short spike above the 55-day high: EMA50 still below EMA200 -> no long
    close = list(np.linspace(200, 100, 250)) + [100] * 60 + [130]
    row = s1.prepare(_bars(close)).iloc[-1]
    assert row["close"] > row["entry_high"] and s1.entry_signal(row) == 0
    assert s1.entry_signal(row, s1.TrendParams(use_ema_filter=False)) == 1


def test_stops():
    assert s1.initial_stop(100, 1, 4) == 90 and s1.initial_stop(100, -1, 4) == 110
    assert s1.trail_stop(1, 90, 130, 4) == 118        # 130 - 3 x 4
    assert s1.trail_stop(1, 120, 125, 4) == 120       # never loosens
    assert s1.trail_stop(-1, 110, 80, 4) == 92


def test_exit_signal():
    row = pd.Series({"close": 95, "exit_low": 96, "exit_high": 120})
    assert s1.exit_signal(row, 1) and not s1.exit_signal(row, -1)


# ---- S3 rules -----------------------------------------------------------------

def test_ratio_signals_and_exits():
    p = s3.RatioParams(use_regime=False)
    row = pd.Series({"z": -2.3, "sd": 0.05, "regime_ok": False})
    assert s3.entry_signal(row, p) == 1                         # gold cheap: long gold / short silver
    assert s3.entry_signal(row, s3.RatioParams()) == 0          # regime filter blocks it
    assert s3.entry_signal(pd.Series({"z": 2.4, "sd": 0.05, "regime_ok": True})) == -1
    assert s3.exit_reason(pd.Series({"z": -0.3}), 1, 3) == "target"
    assert s3.exit_reason(pd.Series({"z": -3.6}), 1, 3) == "stop"
    assert s3.exit_reason(pd.Series({"z": 3.6}), 1, 3) == "target"   # far side is a win, not a stop
    assert s3.exit_reason(pd.Series({"z": -1.5}), 1, 30) == "time"
    assert s3.exit_reason(pd.Series({"z": -1.5}), 1, 5) is None


def test_ratio_prepare_units():
    idx = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(0)
    gold = pd.Series(5000 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))), index=idx)
    silver = gold / 80 * np.exp(rng.normal(0, 0.02, 400))
    sig = s3.prepare(gold, silver)
    assert sig["spread"].mean() == pytest.approx(np.log(80), abs=0.01)
    assert sig["regime_ok"].iloc[-1]                     # i.i.d. noise around a constant ratio reverts fast
