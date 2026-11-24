"""STUBS for the parts of Phase 1 that need your Kite Connect keys (Phase 1b).

Built once KITE_API_KEY / KITE_API_SECRET are in .env and the daily login works:
  - fetch_kite_daily_continuous: Kite "continuous" daily candles for expired
    futures (cross-check for the MCX bhavcopy history, PLAN §6)
  - fetch_kite_minute_history:   1-minute candles for contracts still trading
  - MinuteBarRecorder:           saves every 1-minute bar from the live feed
  - activity_scan:               volume/volatility per 15-minute slot, which
                                 decides the S2 candidate windows (PLAN §3.2)

All of them go through the read-only broker interface (bot.broker.broker_base).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from bot.broker.broker_base import ReadOnlyBroker

_MSG = "Phase 1b: needs your Kite Connect keys in .env. Not built yet."


def fetch_kite_daily_continuous(broker: ReadOnlyBroker, symbol: str, start: date, end: date) -> pd.DataFrame:
    raise NotImplementedError(_MSG)


def fetch_kite_minute_history(broker: ReadOnlyBroker, tradingsymbol: str, start: date, end: date) -> pd.DataFrame:
    raise NotImplementedError(_MSG)


class MinuteBarRecorder:
    def __init__(self, broker: ReadOnlyBroker, symbols: list[str]):
        raise NotImplementedError(_MSG)


def activity_scan(minute_bars: pd.DataFrame, slot_minutes: int = 15) -> pd.DataFrame:
    raise NotImplementedError(_MSG)
