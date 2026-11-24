"""Trading cost model (PLAN §5.1). Every rate comes from settings.yaml -> costs.

Per order (each side of a trade):
  brokerage   0.03% of turnover or Rs 20, whichever is lower (Zerodha), or flat Rs 20
  exchange    MCX transaction charge, % of turnover
  SEBI fee    Rs 10 per crore of turnover
  CTT         on the SELL side only (non-agri futures)
  stamp duty  on the BUY side only
  GST         18% on brokerage + exchange charge + SEBI fee
  slippage    ticks x tick size x multiplier x lots; entries get an extra allowance
              because you place orders by hand; more around US events
Fixed: the Kite Connect data plan (Rs 500/month) is charged against the account.

`stress_multiplier` (e.g. 2.0) scales every cost for the "profitable at 2x costs" gate.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Literal

from bot.config import Costs, Instrument

Side = Literal["buy", "sell"]
Direction = Literal["long", "short"]
SEBI_PER_RUPEE = 1 / 1e7   # per crore -> per rupee


@dataclass
class CostBreakdown:
    brokerage: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    ctt: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    slippage: float = 0.0

    @property
    def charges(self) -> float:
        """Broker + statutory charges (everything except slippage)."""
        return self.brokerage + self.exchange + self.sebi + self.ctt + self.stamp + self.gst

    @property
    def total(self) -> float:
        return self.charges + self.slippage

    def __add__(self, other: "CostBreakdown") -> "CostBreakdown":
        return CostBreakdown(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})

    def scaled(self, k: float) -> "CostBreakdown":
        return CostBreakdown(**{f.name: getattr(self, f.name) * k for f in fields(self)})


class CostModel:
    def __init__(self, cfg: Costs):
        self.c = cfg
        self.slip_by_symbol = dict(cfg.slippage_ticks_by_symbol)

    def brokerage(self, turnover: float) -> float:
        if self.c.brokerage_mode == "flat":
            return self.c.brokerage_flat_inr if turnover > 0 else 0.0
        return min(self.c.brokerage_pct * turnover, self.c.brokerage_cap_inr)

    def order_charges(self, price: float, lots: int, inst: Instrument, side: Side) -> CostBreakdown:
        turnover = abs(price) * inst.multiplier * lots
        b = self.brokerage(turnover)
        ex = self.c.exchange_txn_pct * turnover
        sebi = self.c.sebi_fee_per_crore_inr * SEBI_PER_RUPEE * turnover
        ctt = self.c.ctt_sell_pct * turnover if side == "sell" else 0.0
        stamp = self.c.stamp_duty_buy_pct * turnover if side == "buy" else 0.0
        gst = self.c.gst_pct * (b + ex + sebi)
        return CostBreakdown(b, ex, sebi, ctt, stamp, gst).scaled(self.c.stress_multiplier)

    def slippage(self, lots: int, inst: Instrument, entry: bool, event: bool = False) -> float:
        ticks = self.slip_by_symbol.get(inst.symbol, self.c.slippage_ticks_per_side)
        if entry:
            ticks += self.c.manual_entry_extra_ticks
        if event:
            ticks *= self.c.event_slippage_multiplier
        return ticks * inst.tick_size * inst.multiplier * lots * self.c.stress_multiplier

    def round_trip(self, inst: Instrument, entry_price: float, exit_price: float, lots: int,
                   direction: Direction, event: bool = False) -> CostBreakdown:
        entry_side: Side = "buy" if direction == "long" else "sell"
        exit_side: Side = "sell" if direction == "long" else "buy"
        cost = self.order_charges(entry_price, lots, inst, entry_side) + \
            self.order_charges(exit_price, lots, inst, exit_side)
        cost.slippage = self.slippage(lots, inst, entry=True, event=event) + \
            self.slippage(lots, inst, entry=False, event=event)
        return cost

    def net_pnl(self, inst: Instrument, entry_price: float, exit_price: float, lots: int,
                direction: Direction, event: bool = False) -> tuple[float, CostBreakdown]:
        sign = 1 if direction == "long" else -1
        gross = sign * (exit_price - entry_price) * inst.multiplier * lots
        cost = self.round_trip(inst, entry_price, exit_price, lots, direction, event)
        return gross - cost.total, cost

    def fixed_costs(self, start: date, end: date) -> float:
        """Data plan cost for [start, end], prorated by days (Rs 500/month = Rs 6,000/year)."""
        days = (end - start).days + 1
        if days <= 0:
            return 0.0
        return self.c.data_plan_inr_per_month * 12 * days / 365.25 * self.c.stress_multiplier
