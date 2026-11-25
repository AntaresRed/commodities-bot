"""Performance metrics and the PLAN §5.4 gates."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252

GATES = {
    # metric: (positional threshold, intraday threshold, "min" or "max")
    "sharpe": (0.7, 1.0, "min"),
    "profit_factor": (1.3, 1.3, "min"),
    "max_drawdown_pct": (20.0, 15.0, "max"),
    "trades": (50, 150, "min"),
    "largest_trade_share_pct": (20.0, 10.0, "max"),
}


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough fall, as a positive fraction."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def summary(equity: pd.Series, trades: pd.DataFrame, capital: float, skipped: pd.DataFrame | None = None,
            costs_paid: float = 0.0, data_plan_paid: float = 0.0) -> dict:
    eq = equity.dropna()
    out: dict = {"start": eq.index[0].date() if len(eq) else None, "end": eq.index[-1].date() if len(eq) else None}
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9) if len(eq) > 1 else float("nan")
    final = float(eq.iloc[-1]) if len(eq) else capital
    out["final_equity"] = final
    out["total_return_pct"] = 100 * (final / capital - 1)
    out["cagr_pct"] = 100 * ((final / capital) ** (1 / years) - 1) if final > 0 and years == years else float("nan")
    r = eq.pct_change().dropna()
    sd = r.std()
    out["sharpe"] = float(r.mean() / sd * math.sqrt(TRADING_DAYS)) if sd and sd > 0 else 0.0
    out["vol_pct"] = float(sd * math.sqrt(TRADING_DAYS) * 100) if sd == sd else float("nan")
    out["max_drawdown_pct"] = 100 * max_drawdown(eq)
    n = len(trades)
    out["trades"] = n
    if n:
        net = trades["net"].astype(float)
        wins, losses = net[net > 0].sum(), -net[net < 0].sum()
        out["win_rate_pct"] = 100 * float((net > 0).mean())
        out["profit_factor"] = float(wins / losses) if losses > 0 else float("inf")
        out["avg_r"] = float(trades["r_multiple"].astype(float).mean())
        out["worst_r"] = float(trades["r_multiple"].astype(float).min())
        total = float(net.sum())
        out["net_trading_pnl"] = total
        out["largest_trade_share_pct"] = 100 * float(net.max() / total) if total > 0 else float("nan")
        out["avg_days_held"] = float((pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"]))
                                     .dt.days.mean())
    else:
        out.update(win_rate_pct=float("nan"), profit_factor=float("nan"), avg_r=float("nan"), worst_r=float("nan"),
                   net_trading_pnl=0.0, largest_trade_share_pct=float("nan"), avg_days_held=float("nan"))
    out["skipped_signals"] = 0 if skipped is None else len(skipped)
    out["costs_paid"] = costs_paid
    out["data_plan_paid"] = data_plan_paid
    return out


def gate_table(s: dict, profitable_at_2x: bool, kind: str = "positional") -> pd.DataFrame:
    col = 0 if kind == "positional" else 1
    rows = []
    for metric, spec in GATES.items():
        thr, how = spec[col], spec[2]
        val = s.get(metric, float("nan"))
        ok = (val >= thr if how == "min" else val <= thr) if val == val else False
        rows.append(dict(gate=metric, value=val, need=f"{'>=' if how == 'min' else '<='} {thr}", passed=bool(ok)))
    rows.append(dict(gate="profitable_at_2x_costs", value=profitable_at_2x, need="yes", passed=bool(profitable_at_2x)))
    return pd.DataFrame(rows)


def period_returns(equity: pd.Series, bounds: list[tuple[str, str]]) -> pd.DataFrame:
    rows = []
    for a, b in bounds:
        e = equity[(equity.index >= a) & (equity.index <= b)].dropna()
        if len(e) < 20:
            continue
        r = e.pct_change().dropna()
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        rows.append(dict(period=f"{a[:4]}-{b[:4]}", return_pct=100 * (e.iloc[-1] / e.iloc[0] - 1),
                         cagr_pct=100 * ((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 and e.iloc[-1] > 0 else np.nan,
                         sharpe=r.mean() / r.std() * math.sqrt(TRADING_DAYS) if r.std() > 0 else 0.0,
                         max_drawdown_pct=100 * max_drawdown(e)))
    return pd.DataFrame(rows)
