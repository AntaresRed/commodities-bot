from datetime import date

import numpy as np
import pandas as pd

from bot.data import quality as q


def _bars(n=30, start="2026-10-01", expiry="2026-12-18", close=None):
    d = pd.bdate_range(start, periods=n)
    c = np.asarray(close if close is not None else 1000 + np.arange(n), dtype=float)
    return pd.DataFrame({"date": d, "symbol": "CRUDEOILM", "expiry": pd.Timestamp(expiry),
                         "open": c, "high": c + 5, "low": c - 5, "close": c,
                         "volume_lots": 100, "traded": True})


def test_clean_data_passes():
    assert q.run_daily_checks(_bars(), today=date(2026, 11, 24)) == []


def test_required_columns():
    out = q.run_daily_checks(_bars().drop(columns=["high"]))
    assert [i.check for i in out] == ["required_columns"]


def test_ohlc_consistency():
    df = _bars()
    df.loc[3, "high"] = df.loc[3, "close"] - 1
    out = q.check_ohlc_consistency(df)
    assert len(out) == 1 and out[0].severity == "error" and out[0].date == df.loc[3, "date"].date()


def test_non_positive_is_warning():
    df = _bars()
    df.loc[5, ["open", "high", "low", "close"]] = [-10, 1, -20, -15]
    out = q.check_non_positive(df)
    assert len(out) == 1 and out[0].severity == "warning"


def test_duplicates_and_future_and_after_expiry():
    df = pd.concat([_bars(), _bars().iloc[[0]]])
    assert len(q.check_duplicates(df)) == 1
    assert q.check_future_dates(_bars(), today=date(2026, 10, 5))
    late = _bars(expiry="2026-10-20")
    assert q.check_after_expiry(late)


def test_missing_days_respects_holidays():
    df = _bars().drop(index=[10, 11, 12, 13])      # 4 business days missing
    out = q.check_missing_days(df, max_gap_business_days=3)
    assert len(out) == 1 and "4 business days" in out[0].detail
    hol = [df.loc[9, "date"] + pd.offsets.BDay(1)]
    assert q.check_missing_days(df, max_gap_business_days=3, holidays=hol) == []


def test_stale_prices_reported_once_per_run():
    c = [100, 101] + [102] * 9 + [103]
    out = q.check_stale_prices(_bars(n=len(c), close=c), max_unchanged=5)
    assert len(out) == 1


def test_untraded_days_do_not_count_as_stale():
    c = [100, 101] + [102] * 9 + [103]
    df = _bars(n=len(c), close=c)
    df.loc[3:9, "traded"] = False
    assert q.check_stale_prices(df, max_unchanged=5) == []


def test_return_outliers_within_contract_only():
    df = _bars()
    df.loc[15, ["open", "high", "low", "close"]] = [2000, 2005, 1995, 2000]
    out = q.check_return_outliers(df, max_abs_return=0.15)
    assert len(out) == 2                                  # jump up and back down
    # a different contract at a different price level must not trigger an outlier
    two = pd.concat([_bars(), _bars(close=1500 + np.arange(30), expiry="2027-01-19")])
    assert q.check_return_outliers(two) == []


def test_low_liquidity():
    df = _bars()
    df.loc[:15, "volume_lots"] = 0
    out = q.check_zero_volume(df, max_share=0.3)
    assert len(out) == 1 and out[0].check == "low_liquidity"


def test_roll_jump_on_adjusted_series():
    s = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=4), "symbol": "X",
                      "adj_close": [100.0, 101, 150, 151], "is_roll_day": [False, False, True, False]})
    assert len(q.check_continuous_rolls(s)) == 1
    s.loc[2, "adj_close"] = 102
    assert q.check_continuous_rolls(s) == []


def test_benchmark_alignment():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2025-01-01", periods=300)
    r = rng.normal(0, 0.01, 300)
    bench = pd.Series(100 * np.cumprod(1 + r), index=idx)
    mcx_good = bench * 1.08                               # duty premium: same returns
    assert q.check_benchmark_alignment(mcx_good, bench, symbol="GOLDTEN") == []
    noise = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)), index=idx)
    mcx_bad = pd.concat([mcx_good.iloc[:150], noise.iloc[150:] * mcx_good.iloc[149] / noise.iloc[150]])
    out = q.check_benchmark_alignment(mcx_bad, bench, symbol="GOLDTEN")
    assert out and out[0].check == "benchmark_mismatch"


def test_issues_frame():
    f = q.issues_frame([q.Issue("a", "error", "X", None, "d")])
    assert list(f.columns) == ["check", "severity", "symbol", "date", "detail"]
    assert q.issues_frame([]).empty
