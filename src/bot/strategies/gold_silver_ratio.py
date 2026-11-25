"""S3: gold/silver ratio mean reversion (PLAN §3.3). Baseline rules only.

Spread  = ln(gold price per gram) - ln(silver price per gram)
Signal  = rolling z-score of the spread
Regime  = the spread must look mean-reverting over the last `regime_window` days:
          Dickey-Fuller t-stat below -2.86 and half-life under 30 days
Entry   = |z| > 2.0: short the expensive metal, buy the cheap one (value-matched)
Exit    = z back inside +/-0.5; stop at |z| > 3.5 or after 30 trading days
Timing  = decided after the close, both legs placed at the next open

Direction +1 = LONG the spread = long gold, short silver (gold cheap: z < -2)
Direction -1 = SHORT the spread = short gold, long silver (gold rich: z > +2)

Note: the hedge ratio is fixed at 1:1 in log terms (value-matched legs), so the
regime test is a stationarity test of that spread, not an Engle-Granger test
with an estimated hedge ratio.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.strategies import indicators as ind


@dataclass(frozen=True)
class RatioParams:
    z_window: int = 90
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 3.5
    max_hold_days: int = 30
    regime_window: int = 250
    adf_t_max: float = -2.86
    max_half_life: float = 30.0
    use_regime: bool = True


def prepare(gold_per_gram: pd.Series, silver_per_gram: pd.Series, p: RatioParams = RatioParams()) -> pd.DataFrame:
    both = pd.concat([gold_per_gram.rename("gold"), silver_per_gram.rename("silver")], axis=1).dropna()
    both = both[(both["gold"] > 0) & (both["silver"] > 0)]
    out = pd.DataFrame(index=both.index)
    out["spread"] = np.log(both["gold"]) - np.log(both["silver"])
    out["z"], out["mean"], out["sd"] = ind.zscore(out["spread"], p.z_window)
    out["adf_t"], out["half_life"] = ind.rolling_mean_reversion(out["spread"], p.regime_window)
    out["regime_ok"] = (out["adf_t"] < p.adf_t_max) & (out["half_life"] < p.max_half_life)
    return out


def entry_signal(row: pd.Series, p: RatioParams = RatioParams()) -> int:
    if pd.isna(row["z"]) or pd.isna(row["sd"]):
        return 0
    if p.use_regime and not bool(row["regime_ok"]):
        return 0
    if row["z"] < -p.entry_z:
        return 1
    if row["z"] > p.entry_z:
        return -1
    return 0


def exit_reason(row: pd.Series, direction: int, days_held: int, p: RatioParams = RatioParams()) -> str | None:
    z = row["z"]
    if pd.isna(z):
        return None
    if abs(z) > p.stop_z and np.sign(z) == -direction:
        return "stop"
    if (direction > 0 and z > -p.exit_z) or (direction < 0 and z < p.exit_z):
        return "target"
    if days_held >= p.max_hold_days:
        return "time"
    return None


def risk_per_unit_value(row: pd.Series, p: RatioParams = RatioParams()) -> float:
    """Loss per rupee of leg value if the spread runs from entry to the z stop."""
    return max(p.stop_z - abs(row["z"]), 0.5) * row["sd"]
