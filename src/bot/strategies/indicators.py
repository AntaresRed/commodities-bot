"""Indicators, written in-house (PLAN §8: a few lines each, no dependency risk).

All functions use only data up to and including each row (no look-ahead).
Breakout levels are shifted by one bar so "close above the 55-day high"
compares today's close with the highs of the 55 days BEFORE today.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift()
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """Wilder's ATR."""
    return true_range(high, low, close).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def prior_high(high: pd.Series, n: int) -> pd.Series:
    return high.rolling(n, min_periods=n).max().shift()


def prior_low(low: pd.Series, n: int) -> pd.Series:
    return low.rolling(n, min_periods=n).min().shift()


def zscore(s: pd.Series, n: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Rolling z-score of s over n bars. Returns (z, mean, std)."""
    m = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std()
    return (s - m) / sd, m, sd


def rolling_mean_reversion(s: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """Rolling OLS of  ds_t = a + b * s_{t-1}  over n bars.

    Returns (t-statistic of b, half-life in bars). A strongly negative t-stat
    (below about -2.86) means the series reverts to a mean (Dickey-Fuller test
    without lags); half-life = -ln 2 / ln(1 + b).
    """
    x = s.shift()
    y = s.diff()
    mx = x.rolling(n, min_periods=n).mean()
    my = y.rolling(n, min_periods=n).mean()
    sxx = (x * x).rolling(n, min_periods=n).mean() - mx * mx
    sxy = (x * y).rolling(n, min_periods=n).mean() - mx * my
    syy = (y * y).rolling(n, min_periods=n).mean() - my * my
    b = sxy / sxx
    resid_var = (syy - b * sxy).clip(lower=0) * n / (n - 2)
    se_b = np.sqrt(resid_var / (sxx * n))
    t = b / se_b
    with np.errstate(invalid="ignore", divide="ignore"):
        hl = -np.log(2) / np.log1p(b)
    hl = hl.where(b < 0)
    return t, hl


def sanitize_ohlc(df: pd.DataFrame, cols=("open", "high", "low", "close")) -> pd.DataFrame:
    """Force high >= open/close/low and low <= open/close/high (fixes bad vendor bars)."""
    o, h, l, c = (df[x] for x in cols)
    out = df.copy()
    out[cols[1]] = pd.concat([o, h, l, c], axis=1).max(axis=1)
    out[cols[2]] = pd.concat([o, h, l, c], axis=1).min(axis=1)
    return out
