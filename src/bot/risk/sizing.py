"""Position sizing (PLAN §3.1, §3.3): whole lots, rounded DOWN, never over budget.

If even one lot would risk more than the budget, the answer is 0 lots and the
signal is skipped (no signal is sent).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def lots_for_risk(equity: float, risk_pct: float, stop_distance: float, multiplier: float) -> int:
    """lots = (equity x risk%) / (stop distance x multiplier), rounded down."""
    if equity <= 0 or stop_distance <= 0 or multiplier <= 0:
        return 0
    budget = equity * risk_pct / 100.0
    return int(math.floor(budget / (stop_distance * multiplier) + 1e-9))


@dataclass(frozen=True)
class PairSize:
    long_lots: int
    short_lots: int
    long_value: float
    short_value: float
    mismatch: float      # |long - short| / max(long, short)


def value_matched_pair(target_value: float, long_lot_value: float, short_lot_value: float,
                       max_mismatch: float = 0.25, max_lots: int = 50) -> PairSize | None:
    """Closest value-matched lot combination with each leg <= target_value.

    Returns None when even 1 lot of either leg exceeds the target, or no
    combination is within `max_mismatch`.
    """
    if min(target_value, long_lot_value, short_lot_value) <= 0:
        return None
    best: PairSize | None = None
    for nl in range(1, max_lots + 1):
        lv = nl * long_lot_value
        if lv > target_value:
            break
        for ns in {max(1, math.floor(lv / short_lot_value)), max(1, math.ceil(lv / short_lot_value))}:
            sv = ns * short_lot_value
            if sv > target_value:
                continue
            mm = abs(lv - sv) / max(lv, sv)
            cand = PairSize(nl, ns, lv, sv, mm)
            # prefer the best match, then the larger position
            if mm <= max_mismatch and (best is None or (round(mm, 3), -lv) < (round(best.mismatch, 3), -best.long_value)):
                best = cand
    return best
