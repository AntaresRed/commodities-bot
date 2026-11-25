"""Read-only broker interface (PLAN §8: data, positions and funds only).

v1 is SIGNALS-ONLY. This interface deliberately has NO order methods, and
`assert_read_only` refuses any adapter that exposes one. Adding automatic
orders later (PLAN §7) is a separate, explicit decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import date, datetime
from typing import Literal

import pandas as pd

Interval = Literal["minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute", "day"]

# Names of order-type operations that must never exist on a v1 adapter.
FORBIDDEN_METHODS = frozenset({
    "place_order", "modify_order", "cancel_order", "exit_order",
    "place_gtt", "modify_gtt", "delete_gtt", "convert_position",
    "place_mf_order", "cancel_mf_order", "order", "buy", "sell",
})


class ReadOnlyViolation(RuntimeError):
    pass


class ReadOnlyBroker(ABC):
    """Everything the bot may ask a broker for. No order placement."""

    name: str = "base"

    @abstractmethod
    def instruments(self, exchange: str = "MCX") -> pd.DataFrame:
        """Contract master: tradingsymbol, name, expiry, lot_size, tick_size, instrument_token, ..."""

    @abstractmethod
    def historical(self, instrument_token: int, start: date | datetime, end: date | datetime,
                   interval: Interval = "day", continuous: bool = False, oi: bool = True) -> pd.DataFrame:
        """OHLCV(+OI) candles. Minute data exists only for live contracts (PLAN §6)."""

    @abstractmethod
    def positions(self) -> pd.DataFrame:
        """Your open positions (read-only), checked every 60 s from Phase 3."""

    @abstractmethod
    def funds(self) -> dict:
        """Available and used margin in the commodity segment."""

    @abstractmethod
    def stream_ticks(self, instrument_tokens: list[int], on_tick: Callable[[list[dict]], None]) -> None:
        """Live ticks over WebSocket (Phase 1b / Phase 3)."""


def assert_read_only(obj: object) -> None:
    """Raise if an adapter (or anything it exposes publicly) has an order method."""
    exposed = {n for n in dir(obj) if not n.startswith("_")}
    bad = sorted(exposed & FORBIDDEN_METHODS)
    if bad:
        raise ReadOnlyViolation(f"{type(obj).__name__} exposes order methods {bad}; v1 is signals-only")
