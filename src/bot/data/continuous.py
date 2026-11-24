"""Back-adjusted continuous contracts (PLAN §3.1, §5.2).

Indicators (Donchian, EMA, ATR) run on the back-adjusted columns (adj_*).
P&L and stops use the ACTUAL contract prices (open/high/low/close), which are
kept on every row together with the contract's expiry.

Roll rule: hold the nearest contract until `roll_days` trading days before its
expiry, then switch to the next one. For bullion, roll_days covers the tender
period plus warning days (config/instruments.yaml). Expiry dates come from the
data itself, so the roll schedule is known in advance (no look-ahead).

Adjustment ("difference" by default): at each roll, gap = new close - old close
on the last day the old contract is held; every earlier price is shifted by the
sum of later gaps. The latest prices therefore equal the actual prices.
"ratio" multiplies instead, which preserves percentage returns.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from bot import paths

log = logging.getLogger(__name__)

Method = Literal["difference", "ratio"]


@dataclass
class RollEvent:
    date: pd.Timestamp          # last day the old contract is held
    old_expiry: pd.Timestamp
    new_expiry: pd.Timestamp
    old_close: float
    new_close: float
    gap: float
    gap_date: pd.Timestamp      # day both closes were taken from


@dataclass
class ContinuousResult:
    series: pd.DataFrame
    rolls: list[RollEvent] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _roll_cutoffs(trading_days: pd.DatetimeIndex, expiries: list[pd.Timestamp], roll_days: int) -> dict:
    """expiry -> first trading day on which that contract is no longer held."""
    out = {}
    for e in expiries:
        pos = int(trading_days.searchsorted(e))  # index of first trading day >= expiry
        if pos >= len(trading_days):
            # expiry after the data ends: hold until the end
            out[e] = pd.Timestamp.max
            continue
        out[e] = trading_days[max(pos - roll_days, 0)]
    return out


def build_continuous(df: pd.DataFrame, roll_days: int = 3, method: Method = "difference") -> ContinuousResult:
    """Build one symbol's continuous series from per-contract daily bars.

    df needs: date, expiry, open, high, low, close (any extra columns are carried).
    """
    if df.empty:
        return ContinuousResult(pd.DataFrame())
    if method not in ("difference", "ratio"):
        raise ValueError("method must be 'difference' or 'ratio'")
    if "symbol" in df.columns and df["symbol"].nunique() > 1:
        raise ValueError("build_continuous takes one symbol at a time")

    bars = df.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
    bars["expiry"] = pd.to_datetime(bars["expiry"]).dt.normalize()
    bars = bars.sort_values(["date", "expiry"]).drop_duplicates(["date", "expiry"], keep="last")
    by_key = bars.set_index(["date", "expiry"])

    trading_days = pd.DatetimeIndex(sorted(bars["date"].unique()))
    expiries = sorted(bars["expiry"].unique())
    cutoff = _roll_cutoffs(trading_days, expiries, roll_days)
    available = bars.groupby("date")["expiry"].apply(set).to_dict()

    issues: list[str] = []
    chosen: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for d in trading_days:
        # nearest contract that is still before its roll cutoff
        target = next((e for e in expiries if d < cutoff[e] and e >= d), None)
        if target is None:
            issues.append(f"{d.date()}: no contract before its roll date")
            continue
        if target not in available.get(d, set()):
            later = sorted(e for e in available.get(d, set()) if e > target)
            if not later:
                issues.append(f"{d.date()}: held contract {target.date()} has no bar")
                continue
            issues.append(f"{d.date()}: held contract {target.date()} has no bar; used {later[0].date()}")
            target = later[0]
        if chosen and target < chosen[-1][1]:
            target = chosen[-1][1]   # never roll backwards to an earlier expiry
            if target not in available.get(d, set()):
                issues.append(f"{d.date()}: held contract {target.date()} has no bar")
                continue
        chosen.append((d, target))

    rows = by_key.loc[chosen].reset_index()
    rolls: list[RollEvent] = []
    gap_after_row: dict[int, float] = {}   # row index of the old contract's last day -> gap
    for i in range(1, len(rows)):
        old_e, new_e = rows.at[i - 1, "expiry"], rows.at[i, "expiry"]
        if new_e == old_e:
            continue
        d_prev, d_new = rows.at[i - 1, "date"], rows.at[i, "date"]
        if (d_prev, new_e) in by_key.index:
            gd, oc, nc = d_prev, rows.at[i - 1, "close"], by_key.at[(d_prev, new_e), "close"]
        elif (d_new, old_e) in by_key.index:
            gd, oc, nc = d_new, by_key.at[(d_new, old_e), "close"], rows.at[i, "close"]
        else:
            issues.append(f"{d_new.date()}: roll {old_e.date()}->{new_e.date()} has no common day; NOT adjusted")
            gd, oc, nc = d_new, np.nan, np.nan
        if method == "difference":
            gap = 0.0 if np.isnan(oc) else float(nc - oc)
        else:
            gap = 1.0 if np.isnan(oc) or oc <= 0 or nc <= 0 else float(nc / oc)
            if not np.isnan(oc) and (oc <= 0 or nc <= 0):
                issues.append(f"{d_new.date()}: non-positive price at roll; ratio adjustment skipped")
        rolls.append(RollEvent(d_prev, old_e, new_e, float(oc), float(nc), gap, gd))
        gap_after_row[i - 1] = gap

    # Adjustment for each row = combined effect of every roll that happens after it
    # (including a roll right after that row's day).
    n = len(rows)
    adj = np.zeros(n) if method == "difference" else np.ones(n)
    acc = 0.0 if method == "difference" else 1.0
    for i in range(n - 1, -1, -1):
        if i in gap_after_row:
            g = gap_after_row[i]
            acc = acc + g if method == "difference" else acc * g
        adj[i] = acc
    out = rows.copy()
    for c in ("open", "high", "low", "close"):
        out[f"adj_{c}"] = out[c] + adj if method == "difference" else out[c] * adj
    out["adj_offset" if method == "difference" else "adj_factor"] = adj
    out["is_roll_day"] = out["expiry"].ne(out["expiry"].shift()) & (out.index > 0)
    out = out.rename(columns={"expiry": "contract_expiry"})
    return ContinuousResult(out, rolls, issues)


def build_all(mcx: pd.DataFrame, roll_days_by_symbol: dict[str, int],
              method: Method = "difference") -> dict[str, ContinuousResult]:
    out = {}
    for sym, g in mcx.groupby("symbol"):
        if sym not in roll_days_by_symbol:
            continue
        out[sym] = build_continuous(g, roll_days=roll_days_by_symbol[sym], method=method)
    return out


def main() -> int:
    from bot.config import load_instruments
    from bot.logging_setup import setup_logging

    setup_logging()
    src = paths.PROCESSED_DIR / "mcx_daily.parquet"
    if not src.exists():
        print("No MCX data yet. Import MCX files first (python -m bot.data.mcx_bhavcopy).")
        return 1
    mcx = pd.read_parquet(src)
    rd = {i.symbol: i.roll_days_before_expiry for i in load_instruments().instruments}
    outdir = paths.PROCESSED_DIR / "continuous"
    outdir.mkdir(parents=True, exist_ok=True)
    for sym, res in build_all(mcx, rd).items():
        res.series.to_parquet(outdir / f"{sym}.parquet", index=False)
        print(f"{sym:11s} {len(res.series):5d} days, {len(res.rolls):3d} rolls, {len(res.issues)} issues")
        for msg in res.issues[:5]:
            print(f"    {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
