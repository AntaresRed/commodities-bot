"""Zerodha Kite Connect adapter: READ-ONLY (data, positions, funds).

The KiteConnect client is kept private (`self.__kite`) and only read calls are
wrapped. There are no order methods here and none must be added in v1.

Status:
  - instruments / positions / funds / historical: built, tested with a fake client.
    historical() splits long ranges into Kite's per-request limits and keeps
    under Kite's 3 requests/second limit for historical data.
  - stream_ticks (live WebSocket feed): STUB, built in Phase 3.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from bot.broker.broker_base import Interval, ReadOnlyBroker, assert_read_only

NEEDS_KEYS = "Not built yet: the live WebSocket feed comes with the signal engine (Phase 3)."

# Kite's maximum date range per historical request, in days
MAX_DAYS_PER_REQUEST = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100,
                        "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000}
MIN_SECONDS_BETWEEN_CALLS = 0.35   # Kite allows 3 historical requests per second


class ZerodhaReadOnly(ReadOnlyBroker):
    name = "zerodha"

    def __init__(self, api_key: str, access_token: str, kite_factory: Callable[..., Any] | None = None,
                 sleep: Callable[[float], None] = _time.sleep, clock: Callable[[], float] = _time.monotonic):
        if kite_factory is None:
            from kiteconnect import KiteConnect
            kite_factory = KiteConnect
        kite = kite_factory(api_key=api_key)
        kite.set_access_token(access_token)
        self.__kite = kite
        self._sleep, self._clock, self._last_call = sleep, clock, None
        assert_read_only(self)

    def __repr__(self) -> str:
        return "ZerodhaReadOnly(api_key=***, access_token=***)"

    def instruments(self, exchange: str = "MCX") -> pd.DataFrame:
        return pd.DataFrame(self.__kite.instruments(exchange))

    def positions(self) -> pd.DataFrame:
        data = self.__kite.positions() or {}
        return pd.DataFrame(data.get("net", []))

    def funds(self) -> dict:
        return self.__kite.margins("commodity")

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = MIN_SECONDS_BETWEEN_CALLS - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def historical(self, instrument_token: int, start: date | datetime, end: date | datetime,
                   interval: Interval = "day", continuous: bool = False, oi: bool = True) -> pd.DataFrame:
        """Candles for [start, end], fetched in chunks. Columns: date, open, high, low, close, volume[, oi].

        Daily candles get a plain date; intraday candles keep Kite's IST timestamps.
        `continuous=True` (daily only) returns expired-contract history spliced together.
        """
        if interval not in MAX_DAYS_PER_REQUEST:
            raise ValueError(f"unsupported interval {interval!r}")
        if continuous and interval != "day":
            raise ValueError("Kite gives continuous data for daily candles only")
        a = start if isinstance(start, datetime) else datetime.combine(start, time(0, 0))
        b = end if isinstance(end, datetime) else datetime.combine(end, time(23, 59, 59))
        step = timedelta(days=MAX_DAYS_PER_REQUEST[interval])
        rows: list[dict] = []
        while a <= b:
            c = min(a + step - timedelta(seconds=1), b)
            self._throttle()
            rows += self.__kite.historical_data(instrument_token, a, c, interval, continuous=continuous, oi=oi) or []
            a = c + timedelta(seconds=1)
        cols = ["date", "open", "high", "low", "close", "volume"] + (["oi"] if oi else [])
        df = pd.DataFrame(rows, columns=None if rows else cols)
        if df.empty:
            return pd.DataFrame(columns=cols)
        df["date"] = pd.to_datetime(df["date"])
        if interval == "day":
            if df["date"].dt.tz is not None:
                df["date"] = df["date"].dt.tz_localize(None)
            df["date"] = df["date"].dt.normalize()
        df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
        return df[[c for c in cols if c in df.columns]]

    def stream_ticks(self, instrument_tokens: list[int], on_tick: Callable[[list[dict]], None]) -> None:
        raise NotImplementedError(NEEDS_KEYS)
