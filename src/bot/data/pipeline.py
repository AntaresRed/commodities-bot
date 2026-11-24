"""Free-data update run by start_bot.bat: benchmarks, MCX import, continuous
series and the data-quality report (reports/data_quality.csv)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime

import pandas as pd

from bot import paths
from bot.config import InstrumentsFile
from bot.data import benchmarks as bm
from bot.data import continuous, mcx_bhavcopy, quality

log = logging.getLogger(__name__)


def _fresh_today(path) -> bool:
    return path.exists() and datetime.fromtimestamp(path.stat().st_mtime).date() == date.today()


def update_benchmarks(force: bool = False) -> list[str]:
    out = paths.PROCESSED_DIR / "benchmarks_daily.parquet"
    if not force and _fresh_today(out):
        return ["Benchmarks: already updated today"]
    frames = bm.download_benchmarks(end=date.today().isoformat())
    if not frames:
        return ["Benchmarks: download failed (internet down or Yahoo unavailable); will retry next start"]
    bm.save(frames)
    return [f"Benchmarks: {t} {len(df)} days to {df['date'].max().date()}" for t, df in frames.items()]


def update_mcx(instruments: InstrumentsFile) -> tuple[pd.DataFrame, list[str]]:
    symbols = {i.symbol for i in instruments.instruments}
    df, rep = mcx_bhavcopy.import_folder(symbols=symbols)
    lines = [rep.summary()]
    if df.empty:
        lines.append("MCX history: no files in data/raw/mcx yet (see README 'MCX history')")
        return df, lines
    df.to_parquet(paths.PROCESSED_DIR / "mcx_daily.parquet", index=False)
    cov = mcx_bhavcopy.coverage(df)
    lines += [f"MCX {r.symbol}: {r.first} .. {r.last}, {r.days} days, {r.contracts} contracts"
              for r in cov.itertuples()]
    return df, lines


def build_continuous_all(mcx: pd.DataFrame, instruments: InstrumentsFile) -> tuple[dict, list[str]]:
    if mcx.empty:
        return {}, []
    rd = {i.symbol: i.roll_days_before_expiry for i in instruments.instruments}
    results = continuous.build_all(mcx, rd)
    outdir = paths.PROCESSED_DIR / "continuous"
    outdir.mkdir(parents=True, exist_ok=True)
    lines = []
    for sym, res in results.items():
        if res.series.empty:
            continue
        s = res.series.assign(symbol=sym)
        s.to_parquet(outdir / f"{sym}.parquet", index=False)
        lines.append(f"Continuous {sym}: {len(s)} days, {len(res.rolls)} rolls, {len(res.issues)} roll issues")
    return results, lines


def quality_report(mcx: pd.DataFrame, cont: dict, instruments: InstrumentsFile) -> list[str]:
    issues: list[quality.Issue] = []
    if not mcx.empty:
        issues += quality.run_daily_checks(mcx)
    for sym, res in cont.items():
        if not res.series.empty:
            issues += quality.check_continuous_rolls(res.series.assign(symbol=sym))
    bpath = paths.PROCESSED_DIR / "benchmarks_daily.parquet"
    if bpath.exists():
        b = pd.read_parquet(bpath)
        for t, g in b.groupby("ticker"):
            g = g.assign(symbol=t)
            # Yahoo often reports a settlement close outside the day's high-low (and has
            # scattered bad bars in INR=X). Only benchmark CLOSES are used, so these are
            # warnings here, while the same check on MCX data stays an error.
            issues += [replace(i, severity="warning", detail=i.detail + " (Yahoo bar; only closes are used)")
                       for i in quality.check_ohlc_consistency(g.dropna(subset=quality.OHLC))]
            issues += quality.check_duplicates(g, ["date", "ticker"])
            issues += quality.check_return_outliers(g, max_abs_return=0.25)
        # MCX vs international benchmark cross-check
        closes = b.pivot_table(index="date", columns="ticker", values="close")
        if "INR=X" in closes:
            by = instruments.by_symbol()
            for sym, res in cont.items():
                inst = by.get(sym)
                if inst is None or res.series.empty or inst.benchmark not in closes:
                    continue
                mc = res.series.set_index("date")["adj_close"]
                bi = bm.in_inr(closes[inst.benchmark].dropna(), closes["INR=X"].dropna(), inst.benchmark_factor)
                issues += quality.check_benchmark_alignment(mc, bi, symbol=sym)
    frame = quality.issues_frame(issues)
    out = paths.REPORTS_DIR / "data_quality.csv"
    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    if frame.empty:
        return [f"Data quality: no issues ({out.name})"]
    counts = frame.groupby(["severity", "check"]).size()
    return [f"Data quality: {len(frame)} issue(s), details in reports/{out.name}"] + \
           [f"  {sev:7s} {chk}: {n}" for (sev, chk), n in counts.items()]


def update_all(instruments: InstrumentsFile, download: bool = True) -> list[str]:
    paths.ensure_dirs()
    lines: list[str] = []
    if download:
        try:
            lines += update_benchmarks()
        except Exception as e:
            log.exception("Benchmark update failed")
            lines.append(f"Benchmarks: failed ({type(e).__name__}); see logs/bot.log")
    mcx, more = update_mcx(instruments)
    lines += more
    cont, more = build_continuous_all(mcx, instruments)
    lines += more
    lines += quality_report(mcx, cont, instruments)
    return lines
