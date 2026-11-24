"""International benchmarks + USD/INR, daily, free (PLAN §6).

COMEX gold (GC=F) and silver (SI=F), NYMEX crude (CL=F) and natural gas (NG=F),
and USD/INR (INR=X), from Yahoo Finance via yfinance. Used for 20+ years of
history to stress-test trend logic and to cross-check MCX prices
(MCX ~ international price x USD/INR + duties).

Caveats:
  - Yahoo's "=F" series are front-month futures spliced WITHOUT back-adjustment,
    so roll days show artificial jumps. Use returns and the quality checks.
  - INR=X is a market USD/INR rate, not the RBI/FBIL reference rate PLAN §6
    mentions (FBIL has no simple free download).
  - Converted prices exclude import duty and GST, so they sit below MCX prices;
    compare returns, not levels.

Command line:  python -m bot.data.benchmarks
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import date

import pandas as pd

from bot import paths

log = logging.getLogger(__name__)

DEFAULT_TICKERS = ("GC=F", "SI=F", "CL=F", "NG=F", "INR=X")
DEFAULT_START = "2000-01-01"
PRICE_COLS = ["open", "high", "low", "close", "volume"]


def _yf_download(ticker: str, start: str, end: str | None) -> pd.DataFrame:
    import yfinance as yf
    return yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False,
                       actions=False, threads=False)


def normalise(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance frame -> columns date, ticker, open, high, low, close, volume."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "ticker", *PRICE_COLS])
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # (field, ticker) or (ticker, field): keep the level that holds field names
        lvl = 0 if "Close" in df.columns.get_level_values(0) else 1
        df.columns = df.columns.get_level_values(lvl)
    df = df.rename(columns=str.lower)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    for c in PRICE_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[PRICE_COLS].dropna(subset=["close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.insert(0, "ticker", ticker)
    return df.rename_axis("date").reset_index()


def download_benchmarks(tickers=DEFAULT_TICKERS, start: str = DEFAULT_START, end: str | None = None,
                        downloader: Callable[[str, str, str | None], pd.DataFrame] = _yf_download
                        ) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            df = normalise(downloader(t, start, end), t)
        except Exception as e:  # one failed ticker should not stop the others
            log.error("Download failed for %s: %s", t, e)
            continue
        if df.empty:
            log.warning("No data returned for %s", t)
            continue
        out[t] = df
        log.info("%s: %d rows %s .. %s", t, len(df), df["date"].min().date(), df["date"].max().date())
    return out


def save(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    paths.BENCHMARK_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for t, df in frames.items():
        df.to_parquet(paths.BENCHMARK_RAW_DIR / f"{t.replace('=', '_')}.parquet", index=False)
    combined = pd.concat(frames.values(), ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        combined.to_parquet(paths.PROCESSED_DIR / "benchmarks_daily.parquet", index=False)
    return combined


def in_inr(bench_close: pd.Series, usdinr_close: pd.Series, factor: float = 1.0,
           max_fill_days: int = 3) -> pd.Series:
    """Benchmark USD price -> INR per MCX quote unit, ex-duty.

    USD/INR is only carried FORWARD (never backward: no look-ahead), for at most
    `max_fill_days` rows, to cover Indian/US holiday mismatches.
    """
    fx = usdinr_close.reindex(bench_close.index.union(usdinr_close.index)).sort_index()
    fx = fx.ffill(limit=max_fill_days).reindex(bench_close.index)
    return (bench_close * fx * factor).rename(f"{bench_close.name or 'bench'}_inr")


def main() -> int:
    from bot.logging_setup import setup_logging

    setup_logging()
    paths.ensure_dirs()
    frames = download_benchmarks(end=(date.today()).isoformat())
    if not frames:
        print("No benchmark data downloaded (internet down or Yahoo blocked?).")
        return 1
    save(frames)
    for t, df in frames.items():
        print(f"{t:6s} {len(df):6d} rows  {df['date'].min().date()} .. {df['date'].max().date()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
