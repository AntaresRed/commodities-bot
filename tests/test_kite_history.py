"""Kite history fetch + downloader, against a fake Kite client (no keys, no network)."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from bot.backtest import proxy
from bot.broker.zerodha import MAX_DAYS_PER_REQUEST, ZerodhaReadOnly
from bot.data import kite_history as kh

TODAY = date(2026, 11, 25)


class FakeKite:
    def __init__(self, api_key):
        self.calls = []

    def set_access_token(self, t):
        pass

    def instruments(self, exchange):
        rows = []
        tok = 1000
        for name, tick, lot in [("GOLD", 1.0, 1), ("GOLDM", 1.0, 1), ("GOLDPETAL", 1.0, 1), ("GOLDGUINEA", 1.0, 1),
                                ("GOLDTEN", 1.0, 1), ("SILVER", 1.0, 30), ("SILVERM", 1.0, 5), ("SILVERMIC", 1.0, 1),
                                ("SILVER100", 1.0, 1), ("CRUDEOIL", 1.0, 100), ("CRUDEOILM", 1.0, 10),
                                ("NATURALGAS", 0.1, 1250), ("NATGASMINI", 0.1, 250)]:
            for k, exp in enumerate([date(2026, 12, 18), date(2027, 1, 19), date(2026, 10, 19)]):
                tok += 1
                rows.append(dict(instrument_token=tok, tradingsymbol=f"{name}{exp:%y%b}FUT".upper(), name=name,
                                 expiry=exp, lot_size=lot, tick_size=tick, instrument_type="FUT",
                                 segment="MCX-FUT", exchange="MCX"))
        rows.append(dict(instrument_token=1, tradingsymbol="GOLD26DEC100000CE", name="GOLD", expiry=date(2026, 12, 18),
                         lot_size=1, tick_size=1.0, instrument_type="CE", segment="MCX-OPT", exchange="MCX"))
        return rows

    def historical_data(self, token, a, b, interval, continuous=False, oi=False):
        self.calls.append((token, a, b, interval, continuous))
        if interval == "day":
            days = pd.bdate_range(max(a, datetime(2015, 1, 1)), b)
            rng = np.random.default_rng(token)
            c = 50000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
            return [dict(date=pd.Timestamp(d).tz_localize("Asia/Kolkata"), open=x, high=x * 1.01, low=x * 0.99,
                         close=x, volume=100, oi=10) for d, x in zip(days, c)]
        mins = pd.date_range(a, min(b, a + timedelta(hours=2)), freq="min", tz="Asia/Kolkata")
        return [dict(date=m, open=1.0, high=1.0, low=1.0, close=1.0, volume=1, oi=1) for m in mins]


@pytest.fixture
def broker():
    slept = []
    clock = iter(np.arange(0, 10_000, 0.1))
    b = ZerodhaReadOnly("k", "t", kite_factory=FakeKite, sleep=slept.append, clock=lambda: next(clock))
    b._slept = slept
    return b


def _kite(b):
    return b._ZerodhaReadOnly__kite


def test_historical_is_chunked_to_kite_limits(broker):
    df = broker.historical(5, date(2015, 1, 1), date(2026, 11, 25), "day", continuous=True)
    calls = _kite(broker).calls
    assert len(calls) == 3                                     # 11.9 years / 2000 days
    for _, a, b, interval, cont in calls:
        assert (b - a) <= timedelta(days=MAX_DAYS_PER_REQUEST["day"]) and cont
    assert calls[0][1] == datetime(2015, 1, 1) and calls[-1][2].date() == date(2026, 11, 25)
    assert df["date"].is_monotonic_increasing and df["date"].is_unique
    assert df["date"].dt.tz is None and (df["date"] == df["date"].dt.normalize()).all()
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "oi"]


def test_minute_history_chunks_and_rate_limit(broker):
    broker.historical(5, date(2026, 1, 1), date(2026, 11, 25), "minute")
    calls = _kite(broker).calls
    assert len(calls) == 6                                      # 329 days / 60
    assert broker._slept and all(s <= 0.35 for s in broker._slept)


def test_bad_requests(broker):
    with pytest.raises(ValueError):
        broker.historical(5, date(2026, 1, 1), date(2026, 2, 1), "minute", continuous=True)
    with pytest.raises(ValueError):
        broker.historical(5, date(2026, 1, 1), date(2026, 2, 1), "2minute")


def test_live_futures_picks_nearest_unexpired(broker):
    ins = broker.instruments("MCX")
    lf = kh.live_futures(ins, "GOLDM", TODAY, n=2)
    assert list(lf["expiry"].dt.date) == [date(2026, 12, 18), date(2027, 1, 19)]   # Oct contract expired


def test_spec_check_flags_differences(broker, instruments):
    lines = kh.compare_specs(broker.instruments("MCX"), instruments, TODAY)
    assert any("NATGASMINI: Kite lot_size 250" in x for x in lines)
    assert not any("tick size" in x for x in lines)


def test_download_all_and_mcx_backtest(broker, instruments, settings, tmp_path):
    lines = kh.download_all(broker, instruments, today=TODAY, out_dir=tmp_path, minute_symbols=("CRUDEOILM",),
                            minute_lookback_days=30)
    assert (tmp_path / "instruments_mcx.parquet").exists()
    assert (tmp_path / "daily_continuous" / "GOLD.parquet").exists()
    assert len(list((tmp_path / "minute").glob("CRUDEOILM*.parquet"))) == 2
    assert any(x.startswith("Daily GOLD:") for x in lines) and any(x.startswith("Minute CRUDEOILM") for x in lines)
    # the backtest runs on the downloaded MCX history
    bases = proxy.kite_bases(instruments, tmp_path)
    assert {"GC=F", "SI=F", "CL=F"} <= set(bases)
    g = kh.load_daily_continuous("GOLD", tmp_path)
    assert bases["GC=F"]["close"].iloc[-1] == pytest.approx(g["close"].iloc[-1] / 10)     # per 10 g -> per gram
    text = proxy.run(settings, instruments, bases=bases, source="kite", write=False)
    assert "MCX backtest" in text and "S1 at 0.5% risk" in text and "S3 at" in text


def test_one_failing_symbol_does_not_stop_download(broker, instruments, tmp_path):
    kite = _kite(broker)
    orig = kite.historical_data

    def flaky(token, *a, **k):
        if token == 1001:                       # first GOLD contract
            raise RuntimeError("boom")
        return orig(token, *a, **k)
    kite.historical_data = flaky
    lines = kh.download_all(broker, instruments, today=TODAY, out_dir=tmp_path, minute_symbols=())
    assert any("Daily GOLD: FAILED" in x for x in lines)
    assert any(x.startswith("Daily SILVER:") and "FAILED" not in x for x in lines)
