"""Data-quality checks for daily bars (PLAN §5.2). Each check returns a list of Issues.

Severity:
  error    the data is wrong and must be fixed or excluded before a backtest
  warning  suspicious; look at it (real events like April 2020 crude can trigger these)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

OHLC = ["open", "high", "low", "close"]


@dataclass(frozen=True)
class Issue:
    check: str
    severity: str           # "error" | "warning"
    symbol: str
    date: date | None
    detail: str


def issues_frame(issues: Iterable[Issue]) -> pd.DataFrame:
    return pd.DataFrame([asdict(i) for i in issues], columns=["check", "severity", "symbol", "date", "detail"])


def _sym(row) -> str:
    s = str(row.get("symbol", "")) if hasattr(row, "get") else ""
    e = row.get("expiry") if hasattr(row, "get") else None
    return f"{s} {pd.Timestamp(e).date()}" if e is not None and not pd.isna(e) else s


def _contract_groups(df: pd.DataFrame):
    keys = [k for k in ("symbol", "expiry") if k in df.columns]
    if not keys:
        yield "", df.sort_values("date")
        return
    for key, g in df.groupby(keys, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        label = " ".join(str(pd.Timestamp(k).date()) if isinstance(k, pd.Timestamp) else str(k) for k in key)
        yield label, g.sort_values("date")


def check_required_columns(df: pd.DataFrame, required: Iterable[str] = ("date", *OHLC)) -> list[Issue]:
    missing = [c for c in required if c not in df.columns]
    return [Issue("required_columns", "error", "", None, f"missing columns: {missing}")] if missing else []


def check_missing_values(df: pd.DataFrame) -> list[Issue]:
    out = []
    for c in ("date", *OHLC):
        if c in df.columns:
            for _, r in df[df[c].isna()].iterrows():
                out.append(Issue("missing_value", "error", _sym(r), None if c == "date" else pd.Timestamp(r["date"]).date(),
                                 f"{c} is empty"))
    return out


def check_ohlc_consistency(df: pd.DataFrame) -> list[Issue]:
    d = df.dropna(subset=OHLC)
    bad = (d["high"] < d[["open", "close", "low"]].max(axis=1)) | (d["low"] > d[["open", "close", "high"]].min(axis=1))
    return [Issue("ohlc_consistency", "error", _sym(r), pd.Timestamp(r["date"]).date(),
                  f"O={r['open']} H={r['high']} L={r['low']} C={r['close']}")
            for _, r in d[bad].iterrows()]


def check_non_positive(df: pd.DataFrame) -> list[Issue]:
    """Zero/negative prices. Real once (MCX crude settled negative on 20 Apr 2020), so a warning."""
    d = df.dropna(subset=OHLC)
    bad = (d[OHLC] <= 0).any(axis=1)
    return [Issue("non_positive_price", "warning", _sym(r), pd.Timestamp(r["date"]).date(),
                  f"close={r['close']}") for _, r in d[bad].iterrows()]


def check_duplicates(df: pd.DataFrame, keys: Iterable[str] | None = None) -> list[Issue]:
    keys = list(keys or [k for k in ("date", "symbol", "expiry") if k in df.columns])
    dup = df[df.duplicated(keys, keep=False)]
    seen = dup.drop_duplicates(keys)
    return [Issue("duplicate_rows", "error", _sym(r), pd.Timestamp(r["date"]).date(),
                  f"{int((dup[keys] == r[keys]).all(axis=1).sum())} rows share {keys}")
            for _, r in seen.iterrows()]


def check_future_dates(df: pd.DataFrame, today: date | None = None) -> list[Issue]:
    today = pd.Timestamp(today or date.today())
    return [Issue("future_date", "error", _sym(r), pd.Timestamp(r["date"]).date(), "date is in the future")
            for _, r in df[pd.to_datetime(df["date"]) > today].iterrows()]


def check_after_expiry(df: pd.DataFrame) -> list[Issue]:
    if "expiry" not in df.columns:
        return []
    d = df[pd.to_datetime(df["date"]) > pd.to_datetime(df["expiry"])]
    return [Issue("bar_after_expiry", "error", _sym(r), pd.Timestamp(r["date"]).date(), "bar dated after its expiry")
            for _, r in d.iterrows()]


def check_missing_days(df: pd.DataFrame, max_gap_business_days: int = 3,
                       holidays: Iterable[date] = ()) -> list[Issue]:
    """Gaps inside a contract's life longer than N business days (holidays excluded)."""
    hol = np.array([np.datetime64(pd.Timestamp(h).date()) for h in holidays], dtype="datetime64[D]")
    out = []
    for label, g in _contract_groups(df):
        dts = pd.to_datetime(g["date"]).values.astype("datetime64[D]")
        if len(dts) < 2:
            continue
        gaps = np.busday_count(dts[:-1], dts[1:], holidays=hol) - 1
        for i in np.where(gaps > max_gap_business_days)[0]:
            out.append(Issue("missing_days", "warning", label, pd.Timestamp(dts[i + 1]).date(),
                             f"{int(gaps[i])} business days missing after {pd.Timestamp(dts[i]).date()}"))
    return out


def check_stale_prices(df: pd.DataFrame, max_unchanged: int = 5) -> list[Issue]:
    """Close unchanged for more than N days in a row while the contract traded."""
    out = []
    for label, g in _contract_groups(df):
        if "traded" in g.columns:
            g = g[g["traded"].astype(bool)]
        same = g["close"].diff().eq(0)
        run = same.groupby((~same).cumsum()).cumsum()
        hits = g[run == max_unchanged]   # report once per run, when it reaches the limit
        out += [Issue("stale_price", "warning", label, pd.Timestamp(r["date"]).date(),
                      f"close {r['close']} unchanged for {max_unchanged}+ traded days") for _, r in hits.iterrows()]
    return out


def check_return_outliers(df: pd.DataFrame, max_abs_return: float = 0.15,
                          price_col: str = "close") -> list[Issue]:
    """Day-on-day move bigger than max_abs_return within one contract (or one series)."""
    out = []
    for label, g in _contract_groups(df):
        p = g[price_col].astype(float)
        prev = p.shift()
        ret = (p - prev) / prev.abs()
        for idx in ret[ret.abs() > max_abs_return].index:
            out.append(Issue("return_outlier", "warning", label, pd.Timestamp(g.at[idx, "date"]).date(),
                             f"{price_col} moved {ret[idx]:+.1%} ({prev[idx]} -> {p[idx]})"))
    return out


def check_zero_volume(df: pd.DataFrame, max_share: float = 0.3, min_days: int = 20) -> list[Issue]:
    """Contracts where too many days had no trades (liquidity filter input, PLAN §1.1)."""
    if "volume_lots" not in df.columns:
        return []
    out = []
    for label, g in _contract_groups(df):
        if len(g) < min_days:
            continue
        share = float((g["volume_lots"].fillna(0) <= 0).mean())
        if share > max_share:
            out.append(Issue("low_liquidity", "warning", label, None,
                             f"{share:.0%} of {len(g)} days had zero volume"))
    return out


def check_continuous_rolls(cont: pd.DataFrame, max_abs_return: float = 0.15) -> list[Issue]:
    """On roll days the ADJUSTED series must not jump; a jump means a bad adjustment."""
    if "is_roll_day" not in cont.columns:
        return []
    p = cont["adj_close"].astype(float)
    prev = p.shift()
    ret = (p - prev) / prev.abs()
    bad = cont["is_roll_day"].astype(bool) & (ret.abs() > max_abs_return)
    sym = str(cont["symbol"].iloc[0]) if "symbol" in cont.columns and len(cont) else ""
    return [Issue("roll_jump", "error", sym, pd.Timestamp(cont.at[i, "date"]).date(),
                  f"adjusted close moved {ret[i]:+.1%} on a roll day") for i in cont.index[bad]]


def check_benchmark_alignment(mcx_close: pd.Series, bench_inr: pd.Series, window: int = 60,
                              min_corr: float = 0.5, symbol: str = "") -> list[Issue]:
    """Rolling correlation of daily returns between MCX and benchmark x USD/INR.

    MCX trades until ~11:30 PM IST so its daily close overlaps most of the US session;
    returns should be strongly correlated. A low stretch suggests bad data or a wrong
    symbol/unit mapping. Reported once per low stretch.
    """
    both = pd.concat([mcx_close.rename("mcx"), bench_inr.rename("bench")], axis=1).dropna()
    if len(both) < window + 1:
        return []
    r = both.pct_change().dropna()
    corr = r["mcx"].rolling(window).corr(r["bench"])
    low = corr < min_corr
    starts = low & ~low.shift(fill_value=False)
    return [Issue("benchmark_mismatch", "warning", symbol, pd.Timestamp(d).date(),
                  f"{window}-day return correlation fell to {corr[d]:.2f}") for d in corr.index[starts]]


def run_daily_checks(df: pd.DataFrame, today: date | None = None, holidays: Iterable[date] = (),
                     max_abs_return: float = 0.15) -> list[Issue]:
    """All per-contract checks on MCX-style daily bars."""
    req = check_required_columns(df)
    if req:
        return req
    issues = []
    issues += check_missing_values(df)
    d = df.dropna(subset=["date"])
    issues += check_duplicates(d)
    issues += check_future_dates(d, today)
    issues += check_after_expiry(d)
    issues += check_ohlc_consistency(d)
    issues += check_non_positive(d)
    issues += check_missing_days(d, holidays=holidays)
    issues += check_stale_prices(d)
    issues += check_return_outliers(d, max_abs_return=max_abs_return)
    issues += check_zero_volume(d)
    return issues
