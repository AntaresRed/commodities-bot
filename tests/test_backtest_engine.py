import numpy as np
import pandas as pd
import pytest

from bot.backtest import metrics
from bot.backtest.costs import CostModel
from bot.backtest.engine import Contract, Market, run_ratio, run_trend
from bot.config import Costs
from bot.strategies.gold_silver_ratio import RatioParams
from bot.strategies.trend import TrendParams

FAST = TrendParams(breakout=10, exit_breakout=5, ema_fast=5, ema_slow=20, atr_n=5)
ZERO = CostModel(Costs(brokerage_pct=0, brokerage_cap_inr=0, ctt_sell_pct=0, exchange_txn_pct=0,
                       sebi_fee_per_crore_inr=0, stamp_duty_buy_pct=0, gst_pct=0, slippage_ticks_per_side=0,
                       manual_entry_extra_ticks=0, data_plan_inr_per_month=0))


def _bars(close, spread=1.0, start="2021-01-01"):
    c = pd.Series(np.asarray(close, dtype=float), index=pd.bdate_range(start, periods=len(close)))
    return pd.DataFrame({"open": c, "high": c + spread, "low": c - spread, "close": c})


def _market(bars, inst, scale=1.0):
    return Market("gold", "bullion", bars, [Contract(inst, bars * scale, scale)])


def test_uptrend_produces_profitable_long_filled_next_open(inst):
    close = list(np.linspace(1000, 1100, 40)) + list(np.linspace(1100, 1400, 60))
    bars = _bars(close, spread=3)
    res = run_trend([_market(bars, inst["GOLDPETAL"])], ZERO, 200000, 1.0, FAST, charge_data_plan=False)
    t = res.trades
    assert len(t) >= 1 and (t["direction"] == 1).all()
    first = t.iloc[0]
    # signal on a close, fill at the NEXT bar's open
    assert first["entry_date"] > bars.index[FAST.ema_slow]
    assert first["entry_price"] == pytest.approx(bars.loc[first["entry_date"], "open"])
    assert t["net"].sum() > 0
    assert res.equity.iloc[-1] == pytest.approx(200000 + t["net"].sum())


def test_gap_through_stop_fills_at_open(inst):
    close = list(np.linspace(1000, 1200, 60))
    bars = _bars(close, spread=2)
    # crash the open far below any stop on one day
    crash = bars.index[50]
    bars.loc[crash, ["open", "high", "low", "close"]] = [900, 905, 890, 900]
    res = run_trend([_market(bars, inst["GOLDPETAL"])], ZERO, 200000, 1.0, FAST, charge_data_plan=False)
    hit = res.trades[res.trades["exit_date"] == crash].iloc[0]
    assert hit["exit_reason"] == "stop" and hit["exit_price"] == 900
    assert hit["r_multiple"] < -1                       # gap loss bigger than planned risk


def test_signal_skipped_when_one_lot_is_over_budget(inst):
    close = list(np.linspace(100_000, 150_000, 80))   # Rs/kg: 1 lot of SILVERMIC moves Rs 1 per point
    bars = _bars(close, spread=200)
    res = run_trend([_market(bars, inst["SILVERMIC"])], ZERO, 20000, 0.5, FAST, charge_data_plan=False)
    assert res.trades.empty
    assert len(res.skipped) >= 1 and "over the risk budget" in res.skipped.iloc[0]["reason"]
    assert res.equity.iloc[-1] == 20000


def test_costs_and_data_plan_are_charged(inst, settings):
    close = list(np.linspace(1000, 1100, 40)) + list(np.linspace(1100, 1400, 60))
    bars = _bars(close, spread=3)
    cm = CostModel(settings.costs)
    res = run_trend([_market(bars, inst["GOLDPETAL"])], cm, 200000, 1.0, FAST, charge_data_plan=True)
    months = len({(d.year, d.month) for d in bars.index})
    assert res.data_plan_paid == pytest.approx(500 * months)
    assert res.costs_paid > 0
    gross = res.trades["gross"].sum()
    assert res.equity.iloc[-1] == pytest.approx(200000 + gross - res.costs_paid - res.data_plan_paid)


def test_contract_choice_uses_most_of_the_budget(inst):
    close = list(np.linspace(1000, 1100, 40)) + list(np.linspace(1100, 1400, 60))
    base = _bars(close, spread=3)              # INR per gram
    contracts = [Contract(inst["GOLDPETAL"], base * 1, 1), Contract(inst["GOLDTEN"], base * 10, 10)]
    m = Market("gold", "bullion", base, contracts)
    res = run_trend([m], ZERO, 200000, 1.0, FAST, charge_data_plan=False)
    first = res.trades.iloc[0]
    assert first["risk_inr"] <= 2000 + 1e-6
    assert first["risk_inr"] >= 2000 - first["risk_inr"] / max(int(first["lots"]), 1) - 1e-6


def test_ratio_engine_round_trip(inst):
    n = 900
    idx = pd.bdate_range("2020-01-01", periods=n)
    t = np.arange(n)
    gold_g = pd.Series(5000.0, index=idx)
    ratio = 80 * np.exp(0.05 * np.sin(t / 15))          # oscillating, mean-reverting ratio
    silver_g = gold_g / ratio
    g_inst, s_inst = inst["GOLDPETAL"], inst["SILVER100"]

    def bars(x):
        return pd.DataFrame({"open": x, "high": x, "low": x, "close": x})
    g = Contract(g_inst, bars(gold_g * g_inst.quote_grams), g_inst.quote_grams)
    s = Contract(s_inst, bars(silver_g * s_inst.quote_grams), s_inst.quote_grams)
    p = RatioParams(z_window=250, entry_z=1.2, exit_z=0.3, stop_z=3.5, max_hold_days=200, use_regime=False)
    res = run_ratio(g, s, ZERO, 1_000_000, 2.0, p, charge_data_plan=False)
    tr = res.trades
    assert len(tr) >= 3 and set(tr["direction"]) == {1, -1}
    assert (tr["exit_reason"] != "stop").all()
    # buying the cheap leg of a reverting ratio pays when it reverts to target
    assert (tr.loc[tr["exit_reason"] == "target", "gross"] > 0).all()
    assert tr["net"].sum() > 0
    assert res.equity.iloc[-1] == pytest.approx(1_000_000 + res.trades["net"].sum())


def test_metrics_and_gates():
    eq = pd.Series([100.0, 110, 99, 120], index=pd.bdate_range("2021-01-01", periods=4))
    assert metrics.max_drawdown(eq) == pytest.approx(0.1)
    trades = pd.DataFrame({"net": [300.0, -100, -100], "r_multiple": [3, -1, -1],
                           "entry_date": eq.index[:3], "exit_date": eq.index[1:]})
    s = metrics.summary(eq, trades, 100.0)
    assert s["profit_factor"] == pytest.approx(1.5) and s["largest_trade_share_pct"] == pytest.approx(300)
    g = metrics.gate_table(s, profitable_at_2x=True)
    assert set(g["gate"]) >= {"sharpe", "profit_factor", "trades", "profitable_at_2x_costs"}
    assert not g.set_index("gate").loc["trades", "passed"]
