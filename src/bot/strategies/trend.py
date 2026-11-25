"""S1: multi-commodity trend following (PLAN §3.1). Baseline rules only.

Entry long : close > highest high of the previous 55 days AND EMA50 > EMA200
Entry short: close < lowest low of the previous 55 days  AND EMA50 < EMA200
Initial stop: 2.5 x ATR(20) from entry
Exit: 3 x ATR chandelier trailing stop, or the opposite 20-day breakout
Timing: decided after the daily close, acted on at the next open (8:30 AM message)

Indicators run on the back-adjusted series; stops and P&L use actual prices.
The same functions are used by the backtest and (Phase 3) by live signals.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bot.strategies import indicators as ind


@dataclass(frozen=True)
class TrendParams:
    breakout: int = 55
    exit_breakout: int = 20
    ema_fast: int = 50
    ema_slow: int = 200
    atr_n: int = 20
    stop_atr: float = 2.5
    trail_atr: float = 3.0
    use_ema_filter: bool = True


def prepare(bars: pd.DataFrame, p: TrendParams = TrendParams()) -> pd.DataFrame:
    """bars: open/high/low/close (back-adjusted for MCX) indexed by date."""
    b = ind.sanitize_ohlc(bars)
    out = pd.DataFrame(index=b.index)
    out["close"] = b["close"]
    out["ema_fast"] = ind.ema(b["close"], p.ema_fast)
    out["ema_slow"] = ind.ema(b["close"], p.ema_slow)
    out["atr"] = ind.atr(b["high"], b["low"], b["close"], p.atr_n)
    out["entry_high"] = ind.prior_high(b["high"], p.breakout)
    out["entry_low"] = ind.prior_low(b["low"], p.breakout)
    out["exit_high"] = ind.prior_high(b["high"], p.exit_breakout)
    out["exit_low"] = ind.prior_low(b["low"], p.exit_breakout)
    return out


def entry_signal(row: pd.Series, p: TrendParams = TrendParams()) -> int:
    """+1 long, -1 short, 0 nothing. Needs all indicators to be available."""
    need = ("ema_fast", "ema_slow", "atr", "entry_high", "entry_low")
    if any(pd.isna(row[k]) for k in need):
        return 0
    up = row["ema_fast"] > row["ema_slow"] if p.use_ema_filter else True
    down = row["ema_fast"] < row["ema_slow"] if p.use_ema_filter else True
    if row["close"] > row["entry_high"] and up:
        return 1
    if row["close"] < row["entry_low"] and down:
        return -1
    return 0


def stop_distance(atr: float, p: TrendParams = TrendParams()) -> float:
    return p.stop_atr * atr


def initial_stop(entry: float, direction: int, atr: float, p: TrendParams = TrendParams()) -> float:
    return entry - direction * stop_distance(atr, p)


def trail_stop(direction: int, current_stop: float, extreme: float, atr: float,
               p: TrendParams = TrendParams()) -> float:
    """Chandelier stop from the best price since entry; it only ever tightens."""
    chandelier = extreme - direction * p.trail_atr * atr
    return max(current_stop, chandelier) if direction > 0 else min(current_stop, chandelier)


def exit_signal(row: pd.Series, direction: int) -> bool:
    """Opposite 20-day breakout on the close."""
    if direction > 0:
        return pd.notna(row["exit_low"]) and row["close"] < row["exit_low"]
    return pd.notna(row["exit_high"]) and row["close"] > row["exit_high"]
