import numpy as np
import pandas as pd
import pytest

from bot.config import TROY_OUNCE_GRAMS
from bot.data import benchmarks as bm


def _yf_like(multi=True):
    idx = pd.DatetimeIndex(["2026-11-18", "2026-11-19", "2026-11-20"], name="Date")
    data = {"Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0.5, 1.5, 2.5], "Close": [1.5, 2.5, 3.5],
            "Adj Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30]}
    df = pd.DataFrame(data, index=idx)
    if multi:
        df.columns = pd.MultiIndex.from_product([df.columns, ["GC=F"]], names=["Price", "Ticker"])
    return df


@pytest.mark.parametrize("multi", [True, False])
def test_normalise(multi):
    out = bm.normalise(_yf_like(multi), "GC=F")
    assert list(out.columns) == ["date", "ticker", "open", "high", "low", "close", "volume"]
    assert out["close"].tolist() == [1.5, 2.5, 3.5] and (out["ticker"] == "GC=F").all()


def test_download_uses_injected_downloader_and_survives_failures():
    def fake(t, start, end):
        if t == "NG=F":
            raise RuntimeError("boom")
        if t == "CL=F":
            return pd.DataFrame()
        return _yf_like()
    out = bm.download_benchmarks(("GC=F", "NG=F", "CL=F"), downloader=fake)
    assert list(out) == ["GC=F"]


def test_in_inr_conversion_and_no_backfill():
    d = pd.to_datetime(["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"])
    gold = pd.Series([2000.0, 2010.0, 2020.0, 2030.0], index=d, name="GC=F")
    fx = pd.Series([np.nan, 84.0, np.nan, 85.0], index=d).dropna()   # 16th missing, 18th missing
    out = bm.in_inr(gold, fx, factor=10 / TROY_OUNCE_GRAMS)
    assert np.isnan(out.iloc[0])                                     # never filled backwards
    assert out.iloc[1] == pytest.approx(2010 * 84 * 10 / TROY_OUNCE_GRAMS)
    assert out.iloc[2] == pytest.approx(2020 * 84 * 10 / TROY_OUNCE_GRAMS)   # forward-filled
    assert out.iloc[3] == pytest.approx(2030 * 85 * 10 / TROY_OUNCE_GRAMS)


def test_forward_fill_limit():
    d = pd.date_range("2026-11-02", periods=6, freq="B")
    s = pd.Series(1.0, index=d)
    fx = pd.Series([80.0], index=d[:1])
    out = bm.in_inr(s, fx, max_fill_days=2)
    assert out.notna().tolist() == [True, True, True, False, False, False]


@pytest.mark.network
def test_live_yahoo_download():
    out = bm.download_benchmarks(("GC=F",), start="2026-01-01")
    assert len(out["GC=F"]) > 100
