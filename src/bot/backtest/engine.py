"""Event-driven daily backtest for S1 (trend) and S3 (gold/silver ratio).

Rules that match how you will actually trade (PLAN §3, §5.2):
  - Signals use completed daily bars only; orders fill at the NEXT day's open.
  - S1 stops sit at the broker (GTT). If a bar trades through the stop it
    fills at the stop, or at the open if the market gaps past it.
  - Whole lots only, rounded down. A signal whose 1 lot is over the risk budget
    is skipped and recorded (small-account test, PLAN §5.3 #8).
  - Every order pays brokerage, statutory charges and slippage (costs.py);
    the Rs 500/month data plan is charged on the first trading day of each month.

For each commodity the engine needs:
  signal  : bars the indicators run on (back-adjusted for MCX), in base units
            (e.g. INR per gram, INR per bbl)
  actual  : per-contract actual prices (open/high/low/close) for fills and P&L
  scale   : contract price = base-unit price x scale (e.g. 10 for a 10 g quote)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from bot.backtest.costs import CostModel
from bot.config import Instrument
from bot.risk.sizing import lots_for_risk, value_matched_pair
from bot.strategies import gold_silver_ratio as s3
from bot.strategies import trend as s1


@dataclass
class Contract:
    inst: Instrument
    actual: pd.DataFrame          # open/high/low/close indexed by date
    scale: float = 1.0


@dataclass
class Market:
    commodity: str
    cluster: str
    signal: pd.DataFrame          # open/high/low/close in base units
    contracts: list[Contract]


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    skipped: pd.DataFrame
    costs_paid: float = 0.0
    data_plan_paid: float = 0.0
    params: dict = field(default_factory=dict)


@dataclass
class _Pos:
    commodity: str
    contract: Contract
    direction: int
    lots: int
    entry_date: pd.Timestamp
    entry_price: float
    stop: float
    risk_inr: float
    entry_cost: float
    extreme: float
    last: float | None = None     # latest close, for mark-to-market


def _side(direction: int, opening: bool) -> str:
    return ("buy" if direction > 0 else "sell") if opening else ("sell" if direction > 0 else "buy")


def _order_cost(cm: CostModel, inst: Instrument, price: float, lots: int, side: str, entry: bool) -> float:
    return cm.order_charges(price, lots, inst, side).total + cm.slippage(lots, inst, entry=entry)


class _Book:
    """Cash, costs and the monthly data-plan charge, shared by both engines."""

    def __init__(self, capital: float, cm: CostModel, charge_data_plan: bool):
        self.cash = capital
        self.cm = cm
        self.costs = 0.0
        self.data_plan = 0.0
        self.charge_data_plan = charge_data_plan
        self._month = None

    def new_day(self, d: pd.Timestamp) -> None:
        m = (d.year, d.month)
        if self.charge_data_plan and m != self._month:
            fee = self.cm.c.data_plan_inr_per_month * self.cm.c.stress_multiplier
            self.cash -= fee
            self.data_plan += fee
        self._month = m

    def pay(self, amount: float) -> None:
        self.cash -= amount
        self.costs += amount


# --------------------------------------------------------------------------- S1

def run_trend(markets: list[Market], cm: CostModel, capital: float, risk_pct: float,
              p: s1.TrendParams = s1.TrendParams(), max_same_direction_per_cluster: int = 2,
              charge_data_plan: bool = True, start: pd.Timestamp | None = None) -> BacktestResult:
    sig = {m.commodity: s1.prepare(m.signal, p) for m in markets}
    by_name = {m.commodity: m for m in markets}
    dates = sorted(set().union(*[set(c.actual.index) for m in markets for c in m.contracts]))
    if start is not None:
        dates = [d for d in dates if d >= start]

    book = _Book(capital, cm, charge_data_plan)
    positions: dict[str, _Pos] = {}
    pending_exit: set[str] = set()
    pending_entry: dict[str, tuple[int, float, float]] = {}   # commodity -> (dir, stop dist base units, equity)
    trades, skipped, equity = [], [], {}

    def close_pos(pos: _Pos, d, price: float, reason: str) -> None:
        cost = _order_cost(cm, pos.contract.inst, price, pos.lots, _side(pos.direction, False), entry=False)
        gross = pos.direction * (price - pos.entry_price) * pos.contract.inst.multiplier * pos.lots
        book.cash += gross
        book.pay(cost)
        net = gross - pos.entry_cost - cost
        trades.append(dict(strategy="S1", commodity=pos.commodity, symbol=pos.contract.inst.symbol,
                           direction=pos.direction, lots=pos.lots, entry_date=pos.entry_date,
                           entry_price=pos.entry_price, exit_date=d, exit_price=price, exit_reason=reason,
                           gross=gross, costs=pos.entry_cost + cost, net=net, risk_inr=pos.risk_inr,
                           r_multiple=net / pos.risk_inr if pos.risk_inr else float("nan")))

    for d in dates:
        book.new_day(d)
        for name, m in by_name.items():
            # 1) exits decided at yesterday's close, at today's open
            if name in pending_exit and name in positions:
                pos = positions[name]
                bar = pos.contract.actual.loc[d] if d in pos.contract.actual.index else None
                if bar is not None:
                    close_pos(pos, d, float(bar["open"]), "signal")
                    del positions[name]
                    pending_exit.discard(name)
            # 2) entries decided at yesterday's close, at today's open
            if name in pending_entry and name not in positions:
                if not any(d in c.actual.index for c in m.contracts):
                    continue          # market closed for this commodity today: keep the order pending
                direction, dist_base, eq_at_signal = pending_entry.pop(name)
                best = None
                for c in m.contracts:
                    if d not in c.actual.index:
                        continue
                    dist = dist_base * c.scale
                    lots = lots_for_risk(eq_at_signal, risk_pct, dist, c.inst.multiplier)
                    if lots < 1:
                        continue
                    used = lots * dist * c.inst.multiplier
                    key = (used, c.inst.multiplier)
                    if best is None or key > best[0]:
                        best = (key, c, lots, dist)
                if best is None:
                    skipped.append(dict(date=d, strategy="S1", commodity=name, direction=direction,
                                        reason="1 lot of every contract is over the risk budget"))
                else:
                    _, c, lots, dist = best
                    px = float(c.actual.loc[d, "open"])
                    cost = _order_cost(cm, c.inst, px, lots, _side(direction, True), entry=True)
                    book.pay(cost)
                    positions[name] = _Pos(name, c, direction, lots, d, px, px - direction * dist,
                                           lots * dist * c.inst.multiplier, cost, px)
            # 3) broker-held stop during the day
            pos = positions.get(name)
            if pos is not None and d in pos.contract.actual.index:
                bar = pos.contract.actual.loc[d]
                o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
                if pos.direction > 0 and l <= pos.stop:
                    close_pos(pos, d, min(o, pos.stop), "stop")
                    del positions[name]
                elif pos.direction < 0 and h >= pos.stop:
                    close_pos(pos, d, max(o, pos.stop), "stop")
                    del positions[name]

        # 4) after the close: trail stops, exit and entry signals
        mtm = book.cash
        open_dirs: dict[tuple[str, int], int] = {}
        for pos in positions.values():
            key = (by_name[pos.commodity].cluster, pos.direction)
            open_dirs[key] = open_dirs.get(key, 0) + 1
        for name, m in by_name.items():
            s = sig[name]
            if d not in s.index:
                continue
            row = s.loc[d]
            pos = positions.get(name)
            if pos is not None and d in pos.contract.actual.index:
                bar = pos.contract.actual.loc[d]
                pos.extreme = max(pos.extreme, float(bar["high"])) if pos.direction > 0 \
                    else min(pos.extreme, float(bar["low"]))
                if pd.notna(row["atr"]):
                    pos.stop = s1.trail_stop(pos.direction, pos.stop, pos.extreme, row["atr"] * pos.contract.scale, p)
                if s1.exit_signal(row, pos.direction):
                    pending_exit.add(name)
            flat_tomorrow = pos is None or name in pending_exit
            if flat_tomorrow:
                direction = s1.entry_signal(row, p)
                if direction and (pos is None or direction != pos.direction):
                    if open_dirs.get((m.cluster, direction), 0) >= max_same_direction_per_cluster:
                        skipped.append(dict(date=d, strategy="S1", commodity=name, direction=direction,
                                            reason="cluster limit"))
                    else:
                        pending_entry[name] = (direction, s1.stop_distance(row["atr"], p), None)
                        open_dirs[(m.cluster, direction)] = open_dirs.get((m.cluster, direction), 0) + 1
        for pos in positions.values():
            if d in pos.contract.actual.index:
                pos.last = float(pos.contract.actual.loc[d, "close"])
            px = pos.last if pos.last is not None else pos.entry_price
            mtm += pos.direction * (px - pos.entry_price) * pos.contract.inst.multiplier * pos.lots
        equity[d] = mtm
        for name, (direction, dist, eq) in list(pending_entry.items()):
            if eq is None:
                pending_entry[name] = (direction, dist, mtm)

    last = dates[-1] if dates else None
    for name, pos in list(positions.items()):
        close_pos(pos, last, pos.last if pos.last is not None else pos.entry_price, "end")
    if dates:
        equity[last] = book.cash
    return BacktestResult(pd.Series(equity, name="equity").sort_index(), pd.DataFrame(trades),
                          pd.DataFrame(skipped), book.costs, book.data_plan,
                          dict(strategy="S1", risk_pct=risk_pct, **asdict(p)))


# --------------------------------------------------------------------------- S3

def run_ratio(gold: Contract, silver: Contract, cm: CostModel, capital: float, risk_pct: float,
              p: s3.RatioParams = s3.RatioParams(), charge_data_plan: bool = True,
              start: pd.Timestamp | None = None) -> BacktestResult:
    """gold/silver contracts' actual prices; the spread uses price per gram."""
    g_gram = gold.actual["close"] / (gold.inst.quote_grams or 1)
    s_gram = silver.actual["close"] / (silver.inst.quote_grams or 1)
    sig = s3.prepare(g_gram, s_gram, p)
    dates = [d for d in sig.index if start is None or d >= start]

    book = _Book(capital, cm, charge_data_plan)
    pos = None          # dict(direction, legs=[(contract, dir, lots, entry_px, entry_cost)], entry_date, risk, held)
    pending_exit_reason: str | None = None
    pending_entry = None
    trades, skipped, equity = [], [], {}

    def legs_for(direction: int):
        return [(gold, direction), (silver, -direction)]

    for d in dates:
        book.new_day(d)
        if not (d in gold.actual.index and d in silver.actual.index):
            continue
        # exits, then entries, at the open
        if pos is not None and pending_exit_reason:
            gross = costs = 0.0
            for c, ldir, lots, epx, ecost in pos["legs"]:
                px = float(c.actual.loc[d, "open"])
                cost = _order_cost(cm, c.inst, px, lots, _side(ldir, False), entry=False)
                g = ldir * (px - epx) * c.inst.multiplier * lots
                book.cash += g
                book.pay(cost)
                gross += g
                costs += cost + ecost
            trades.append(dict(strategy="S3", commodity="gold/silver",
                               symbol=f"{gold.inst.symbol}/{silver.inst.symbol}", direction=pos["direction"],
                               lots=f"{pos['legs'][0][2]}/{pos['legs'][1][2]}", entry_date=pos["entry_date"],
                               entry_price=pos["entry_z"], exit_date=d, exit_price=float(sig.loc[d, "z"]),
                               exit_reason=pending_exit_reason, gross=gross, costs=costs, net=gross - costs,
                               risk_inr=pos["risk"], r_multiple=(gross - costs) / pos["risk"]))
            pos, pending_exit_reason = None, None
        if pos is None and pending_entry is not None:
            direction, target_value, risk_per_value, z = pending_entry
            pending_entry = None
            g_lot = float(gold.actual.loc[d, "open"]) * gold.inst.multiplier
            s_lot = float(silver.actual.loc[d, "open"]) * silver.inst.multiplier
            long_lot, short_lot = (g_lot, s_lot) if direction > 0 else (s_lot, g_lot)
            size = value_matched_pair(target_value, long_lot, short_lot)
            if size is None:
                skipped.append(dict(date=d, strategy="S3", commodity="gold/silver", direction=direction,
                                    reason="no value-matched whole-lot pair within the risk budget"))
            else:
                lots_g, lots_s = (size.long_lots, size.short_lots) if direction > 0 else (size.short_lots, size.long_lots)
                legs = []
                for c, ldir, lots in ((gold, direction, lots_g), (silver, -direction, lots_s)):
                    px = float(c.actual.loc[d, "open"])
                    cost = _order_cost(cm, c.inst, px, lots, _side(ldir, True), entry=True)
                    book.pay(cost)
                    legs.append((c, ldir, lots, px, cost))
                value = max(size.long_value, size.short_value)
                pos = dict(direction=direction, legs=legs, entry_date=d, risk=value * risk_per_value,
                           held=0, entry_z=z)
        # after the close
        row = sig.loc[d]
        mtm = book.cash
        if pos is not None:
            pos["held"] += 1
            for c, ldir, lots, epx, _ in pos["legs"]:
                mtm += ldir * (float(c.actual.loc[d, "close"]) - epx) * c.inst.multiplier * lots
            reason = s3.exit_reason(row, pos["direction"], pos["held"], p)
            if reason:
                pending_exit_reason = reason
        else:
            direction = s3.entry_signal(row, p)
            if direction:
                rpv = s3.risk_per_unit_value(row, p)
                pending_entry = (direction, mtm * risk_pct / 100.0 / rpv, rpv, float(row["z"]))
        equity[d] = mtm

    if pos is not None and dates:
        last = dates[-1]
        gross = costs = 0.0
        for c, ldir, lots, epx, ecost in pos["legs"]:
            px = float(c.actual.loc[last, "close"])
            cost = _order_cost(cm, c.inst, px, lots, _side(ldir, False), entry=False)
            g = ldir * (px - epx) * c.inst.multiplier * lots
            book.cash += g
            book.pay(cost)
            gross += g
            costs += cost + ecost
        trades.append(dict(strategy="S3", commodity="gold/silver", symbol=f"{gold.inst.symbol}/{silver.inst.symbol}",
                           direction=pos["direction"], lots=f"{pos['legs'][0][2]}/{pos['legs'][1][2]}",
                           entry_date=pos["entry_date"], entry_price=pos["entry_z"], exit_date=last,
                           exit_price=float(sig.loc[last, "z"]), exit_reason="end", gross=gross, costs=costs,
                           net=gross - costs, risk_inr=pos["risk"], r_multiple=(gross - costs) / pos["risk"]))
        equity[last] = book.cash
    return BacktestResult(pd.Series(equity, name="equity").sort_index(), pd.DataFrame(trades),
                          pd.DataFrame(skipped), book.costs, book.data_plan,
                          dict(strategy="S3", risk_pct=risk_pct, **asdict(p)))
