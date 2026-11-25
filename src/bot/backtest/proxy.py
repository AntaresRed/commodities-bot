"""PROXY backtest of S1 and S3 on international prices converted to rupees.

This is a code-and-logic check while MCX data is not available yet.
It CANNOT pass or fail a strategy (PLAN §0: "tested" means tested on MCX data):
  - COMEX/NYMEX prices x USD/INR, without Indian import duty/GST (levels ~8-10% low)
  - Yahoo's =F series splice front-month contracts WITHOUT back-adjustment, so
    roll days show artificial jumps; long-side results include roughly the
    interest-rate carry as fake profit (short side the reverse). Energy rolls
    are the worst affected.
  - Yahoo opens are COMEX/NYMEX opens, not the 9:00 AM MCX open.
Parameters are PLAN baseline values; nothing was tuned on this data.

With --source kite the same code runs on REAL MCX prices: Kite's continuous
daily history of the long-history contracts (GOLD, SILVER, CRUDEOIL,
NATURALGAS), converted to per-gram / per-barrel prices for the small contracts.
Kite's continuous series is not back-adjusted either (smaller roll jumps).

Run:  python -m bot.backtest.proxy [--source yahoo|kite]
      (tools\\run_proxy_backtest.bat, tools\\run_mcx_backtest.bat)
Output: reports/<proxy|mcx>_backtest.md and reports/<proxy|mcx>_trades.csv
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date

import pandas as pd

from bot import paths
from bot.backtest import metrics
from bot.backtest.costs import CostModel
from bot.backtest.engine import BacktestResult, Contract, Market, run_ratio, run_trend
from bot.config import TROY_OUNCE_GRAMS, InstrumentsFile, Settings, load_instruments, load_settings
from bot.strategies.gold_silver_ratio import RatioParams
from bot.strategies.indicators import sanitize_ohlc
from bot.strategies.trend import TrendParams

PERIODS = [("2004-01-01", "2013-12-31"), ("2014-01-01", "2023-12-31"), ("2024-01-01", "2026-12-31")]
BASE_UNIT = {  # benchmark ticker -> (commodity, USD-unit -> base-unit divisor)
    "GC=F": ("gold", TROY_OUNCE_GRAMS),     # base unit: INR per gram
    "SI=F": ("silver", TROY_OUNCE_GRAMS),   # base unit: INR per gram
    "CL=F": ("crude_oil", 1.0),             # INR per bbl
    "NG=F": ("natural_gas", 1.0),           # INR per mmBtu
}


def base_unit_bars(bench: pd.DataFrame, ticker: str, max_fx_fill: int = 3) -> pd.DataFrame:
    """Benchmark OHLC (USD) -> INR per base unit, using that day's USD/INR close."""
    px = bench[bench["ticker"] == ticker].set_index("date").sort_index()
    fx = bench[bench["ticker"] == "INR=X"].set_index("date")["close"].sort_index()
    fx = fx.reindex(px.index.union(fx.index)).ffill(limit=max_fx_fill).reindex(px.index)
    div = BASE_UNIT[ticker][1]
    out = px[["open", "high", "low", "close"]].mul(fx, axis=0) / div
    return sanitize_ohlc(out.dropna())


def contract_scale(inst) -> float:
    return float(inst.quote_grams) if inst.quote_grams else 1.0


def yahoo_bases(bench: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """benchmark ticker -> INR per base unit (gram / bbl / mmBtu) bars."""
    return {t: base_unit_bars(bench, t) for t in BASE_UNIT if (bench["ticker"] == t).any()}


def kite_bases(instruments: InstrumentsFile, directory=None) -> dict[str, pd.DataFrame]:
    """Kite continuous daily bars of the history-only contracts, in base units.

    GOLD is quoted per 10 g -> divide by 10 for INR per gram; SILVER per kg -> /1000.
    """
    from bot.data.kite_history import load_daily_continuous
    out = {}
    for i in instruments.instruments:
        if i.role != "history_only" or i.benchmark in out:
            continue
        try:
            df = load_daily_continuous(i.symbol, directory)
        except FileNotFoundError:
            continue
        bars = df.set_index(pd.to_datetime(df["date"]))[["open", "high", "low", "close"]].astype(float)
        bars = bars[(bars[["open", "high", "low"]] != 0).all(axis=1)].sort_index()
        div = float(i.quote_grams) if i.quote_grams else 1.0
        out[i.benchmark] = sanitize_ohlc(bars / div)
    if not out:
        raise FileNotFoundError("No Kite history yet: run tools\\kite_download.bat first.")
    return out


def build_markets(bases: dict[str, pd.DataFrame], instruments: InstrumentsFile, strategy: str = "S1") -> list[Market]:
    markets = []
    for ticker, (commodity, _) in BASE_UNIT.items():
        insts = [i for i in instruments.tradeable() if i.benchmark == ticker and strategy in i.strategies]
        if not insts or ticker not in bases:
            continue
        base = bases[ticker]
        contracts = [Contract(i, base * contract_scale(i), contract_scale(i)) for i in insts]
        markets.append(Market(commodity, insts[0].cluster, base, contracts))
    return markets


def _stressed(settings: Settings, k: float) -> CostModel:
    return CostModel(settings.costs.model_copy(update={"stress_multiplier": k}))


def _fmt(v) -> str:
    if isinstance(v, float):
        return "n/a" if v != v else f"{v:,.2f}"
    return str(v)


def _section(title: str, res: BacktestResult, res2x: BacktestResult, capital: float, kind: str = "positional") -> str:
    s = metrics.summary(res.equity, res.trades, capital, res.skipped, res.costs_paid, res.data_plan_paid)
    s2 = metrics.summary(res2x.equity, res2x.trades, capital, res2x.skipped, res2x.costs_paid, res2x.data_plan_paid)
    gates = metrics.gate_table(s, s2["final_equity"] > capital, kind)
    lines = [f"### {title}", ""]
    yrs = max((res.equity.index[-1] - res.equity.index[0]).days / 365.25, 1e-9) if len(res.equity) > 1 else 1
    for x in (s, s2):
        x["net_pnl_per_year"] = x["net_trading_pnl"] / yrs
    keys = ["start", "end", "final_equity", "total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct", "trades",
            "win_rate_pct", "profit_factor", "avg_r", "worst_r", "avg_days_held", "largest_trade_share_pct",
            "skipped_signals", "costs_paid", "net_pnl_per_year"]
    lines += ["| Metric | 1x costs | 2x costs |", "|---|---:|---:|"]
    lines += [f"| {k} | {_fmt(s[k])} | {_fmt(s2[k])} |" for k in keys]
    lines += ["", "Gates (PLAN §5.4), **proxy only, not a real verdict**:", "",
              "| Gate | Value | Needed | Pass |", "|---|---:|---|:-:|"]
    lines += [f"| {r.gate} | {_fmt(r.value)} | {r.need} | {'✅' if r.passed else '❌'} |" for r in gates.itertuples()]
    if not res.trades.empty:
        by = res.trades.groupby("symbol").agg(trades=("net", "size"), net=("net", "sum"),
                                              win_rate=("net", lambda x: 100 * (x > 0).mean()))
        lines += ["", "| Contract | Trades | Net P&L (Rs) | Win rate % |", "|---|---:|---:|---:|"]
        lines += [f"| {i} | {r.trades} | {r.net:,.0f} | {r.win_rate:.0f} |" for i, r in by.iterrows()]
    if not res.skipped.empty:
        sk = res.skipped.groupby(["commodity", "reason"]).size()
        lines += ["", "Skipped signals:", ""] + [f"- {c}: {n} ({why})" for (c, why), n in sk.items()]
    pr = metrics.period_returns(res.equity, PERIODS)
    if not pr.empty:
        lines += ["", "| Period | Return % | CAGR % | Sharpe | Max DD % |", "|---|---:|---:|---:|---:|"]
        lines += [f"| {r.period} | {r.return_pct:.1f} | {r.cagr_pct:.1f} | {r.sharpe:.2f} | {r.max_drawdown_pct:.1f} |"
                  for r in pr.itertuples()]
    return "\n".join(lines) + "\n"


HEADERS = {
    "yahoo": [
        "# Proxy backtest (international prices x USD/INR), {today}", "",
        "> **Proxy only.** This checks that the strategy code, sizing and costs work. It is NOT the PLAN §5.4",
        "> verdict: that needs MCX data. Known biases: no Indian duty/GST in price levels; Yahoo's front-month",
        "> series are spliced without back-adjustment (fake roll jumps; longs gain ~the carry, energy worst);",
        "> fills at COMEX/NYMEX opens, not MCX's. Baseline PLAN parameters, nothing tuned.", ""],
    "kite": [
        "# MCX backtest (Kite continuous daily history), {today}", "",
        "> **Real MCX prices**, from the long-history contracts (GOLD, SILVER, CRUDEOIL), converted to the",
        "> small contracts' units. Remaining caveats: Kite's continuous series is spliced without back-adjustment",
        "> (each roll shows as a jump, small for bullion); no walk-forward or robustness tests yet; the whole",
        "> period is shown, so this is not the untouched-holdout test (PLAN §5.3). Baseline PLAN parameters.", ""],
}


def run(settings: Settings | None = None, instruments: InstrumentsFile | None = None,
        bench: pd.DataFrame | None = None, write: bool = True, source: str = "yahoo",
        bases: dict[str, pd.DataFrame] | None = None) -> str:
    settings = settings or load_settings()
    instruments = instruments or load_instruments()
    if bases is None:
        if source == "kite":
            bases = kite_bases(instruments)
        else:
            if bench is None:
                path = paths.PROCESSED_DIR / "benchmarks_daily.parquet"
                if not path.exists():
                    raise FileNotFoundError("No benchmark data yet: run start_bot.bat once (it downloads it).")
                bench = pd.read_parquet(path)
            bases = yahoo_bases(bench)
    capital = settings.capital_inr
    cm1, cm2 = _stressed(settings, 1.0), _stressed(settings, 2.0)
    by = instruments.by_symbol()

    out = [line.format(today=f"{date.today():%d %b %Y}") for line in HEADERS[source]] + [
           f"Capital Rs {capital:,.0f}, not compounded beyond what the account earns. Whole lots only. "
           "Trading costs per settings.yaml (brokerage, CTT, exchange, SEBI, stamp, GST, slippage incl. "
           "the manual-entry allowance). '2x costs' doubles trading costs only.", "",
           f"**Data plan hurdle:** the Kite plan (Rs {settings.costs.data_plan_inr_per_month:,.0f}/month = "
           f"Rs {12 * settings.costs.data_plan_inr_per_month:,.0f}/year) is one shared cost for the whole bot, so it is "
           "NOT charged inside each strategy below. The strategies together must make more than this per year "
           "(compare with `net_pnl_per_year`).", ""]
    trade_frames = []

    markets = build_markets(bases, instruments, "S1")
    out.append("## S1 trend following\n")
    out.append("Contracts considered: " + ", ".join(c.inst.symbol for m in markets for c in m.contracts) + "\n")
    for risk in (0.5, 1.0):
        r1 = run_trend(markets, cm1, capital, risk, TrendParams(), charge_data_plan=False)
        r2 = run_trend(markets, cm2, capital, risk, TrendParams(), charge_data_plan=False)
        out.append(_section(f"S1 at {risk}% risk per trade", r1, r2, capital))
        trade_frames.append(r1.trades.assign(run=f"S1_{risk}"))

    gold, silver = by["GOLDPETAL"], by["SILVER100"]
    g = Contract(gold, bases["GC=F"] * contract_scale(gold), contract_scale(gold))
    s = Contract(silver, bases["SI=F"] * contract_scale(silver), contract_scale(silver))
    out.append(f"## S3 gold/silver ratio ({gold.symbol} vs {silver.symbol})\n")
    for use_regime in (True, False):
        for risk in (0.5, 1.0):
            p = replace(RatioParams(), use_regime=use_regime)
            r1 = run_ratio(g, s, cm1, capital, risk, p, charge_data_plan=False)
            r2 = run_ratio(g, s, cm2, capital, risk, p, charge_data_plan=False)
            label = "with regime filter" if use_regime else "WITHOUT regime filter"
            out.append(_section(f"S3 at {risk}% risk, {label}", r1, r2, capital))
            trade_frames.append(r1.trades.assign(run=f"S3_{risk}_{'regime' if use_regime else 'noregime'}"))

    text = "\n".join(out)
    if write:
        paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = "proxy" if source == "yahoo" else "mcx"
        (paths.REPORTS_DIR / f"{stem}_backtest.md").write_text(text, encoding="utf-8")
        frames = [f for f in trade_frames if not f.empty]
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(paths.REPORTS_DIR / f"{stem}_trades.csv", index=False)
    return text


def main(argv: list[str] | None = None) -> int:
    import argparse
    from bot.logging_setup import setup_logging
    ap = argparse.ArgumentParser(description="S1/S3 backtest")
    ap.add_argument("--source", choices=["yahoo", "kite"], default="yahoo",
                    help="yahoo = international proxy prices; kite = real MCX history from Kite")
    args = ap.parse_args(argv)
    setup_logging()
    try:
        run(source=args.source)
    except FileNotFoundError as e:
        print(e)
        return 1
    stem = "proxy" if args.source == "yahoo" else "mcx"
    print(f"Report written to {paths.REPORTS_DIR / f'{stem}_backtest.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
