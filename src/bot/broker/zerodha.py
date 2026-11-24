"""Zerodha Kite Connect adapter: READ-ONLY (data, positions, funds).

The KiteConnect client is kept private (`self.__kite`) and only read calls are
wrapped. There are no order methods here and none must be added in v1.

Status (Phase 1, credential-free part):
  - instruments / positions / funds: thin wrappers, tested with a fake client.
  - historical / stream_ticks: STUBS. They need your Kite keys and are built in
    Phase 1b (chunked history with rate limits, WebSocket feed, minute recorder).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd

from bot.broker.broker_base import Interval, ReadOnlyBroker, assert_read_only

NEEDS_KEYS = ("Not built yet: needs your Kite Connect keys (Phase 1b). "
              "See README 'What happens next'.")


class ZerodhaReadOnly(ReadOnlyBroker):
    name = "zerodha"

    def __init__(self, api_key: str, access_token: str, kite_factory: Callable[..., Any] | None = None):
        if kite_factory is None:
            from kiteconnect import KiteConnect
            kite_factory = KiteConnect
        kite = kite_factory(api_key=api_key)
        kite.set_access_token(access_token)
        self.__kite = kite
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

    def historical(self, instrument_token: int, start: date | datetime, end: date | datetime,
                   interval: Interval = "day", continuous: bool = False, oi: bool = True) -> pd.DataFrame:
        raise NotImplementedError(NEEDS_KEYS)

    def stream_ticks(self, instrument_tokens: list[int], on_tick: Callable[[list[dict]], None]) -> None:
        raise NotImplementedError(NEEDS_KEYS)
