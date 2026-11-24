"""Signal object and its Telegram text (PLAN §3.6)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

SHORT_CODES = {
    "CRUDEOILM": "CRM", "CRUDEOIL": "CRD",
    "NATGASMINI": "NGM", "NATURALGAS": "NG",
    "GOLDPETAL": "GDP", "GOLDGUINEA": "GDG", "GOLDTEN": "GDT", "GOLDM": "GDM", "GOLD": "GD",
    "SILVER100": "S100", "SILVERMIC": "SVM", "SILVERM": "SVR", "SILVER": "SV",
}


def make_signal_id(strategy: str, day: date, symbol: str, seq: int) -> str:
    """e.g. S2-20261124-CRM-01. Unique per setup (duplicate-signal guard, PLAN §4)."""
    code = SHORT_CODES.get(symbol, symbol[:3].upper())
    return f"{strategy}-{day:%Y%m%d}-{code}-{seq:02d}"


def inr(x: float, decimals: int = 0) -> str:
    """Indian-style grouping: 1234567.8 -> 12,34,567.8"""
    neg = x < 0
    s = f"{abs(x):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = whole + (f".{frac}" if frac else "")
    return ("-" if neg else "") + out


@dataclass
class Signal:
    signal_id: str
    strategy: Literal["S1", "S2", "S3"]
    side: Literal["BUY", "SELL"]
    symbol: str
    contract: str                 # e.g. "DEC-26"
    lots: int
    entry: float
    stop: float
    risk_inr: float
    equity_inr: float
    reason: str
    entry_type: str = "limit"
    valid_till: str | None = None
    target: float | None = None
    target_label: str | None = None
    squareoff_alert: str | None = None
    price_decimals: int = 0
    test: bool = False
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lots < 1:
            raise ValueError("a signal needs at least 1 lot")
        if self.side == "BUY" and not self.stop < self.entry:
            raise ValueError("BUY stop must be below entry")
        if self.side == "SELL" and not self.stop > self.entry:
            raise ValueError("SELL stop must be above entry")

    @property
    def risk_pct(self) -> float:
        return 100.0 * self.risk_inr / self.equity_inr

    def to_dict(self) -> dict:
        return asdict(self)

    def format(self) -> str:
        d = self.price_decimals
        icon = "🟢" if self.side == "BUY" else "🔴"
        lot_word = "lot" if self.lots == 1 else "lots"
        lines = []
        if self.test:
            lines.append("🧪 TEST SIGNAL: made-up prices, do not trade")
        lines.append(f"{icon} {self.strategy} {self.side}  {self.symbol} {self.contract}  · {self.lots} {lot_word}")
        entry = f"Entry : {self.entry_type} ₹{inr(self.entry, d)}"
        if self.valid_till:
            entry += f" (valid till {self.valid_till})"
        lines.append(entry)
        lines.append(f"Stop  : ₹{inr(self.stop, d)}   (risk ₹{inr(self.risk_inr)} = {self.risk_pct:.2f}% of equity)")
        if self.target is not None:
            tgt = f"Target: ₹{inr(self.target, d)}"
            if self.target_label:
                tgt += f"   ({self.target_label})"
            if self.squareoff_alert:
                tgt += f"   · Square-off alert at {self.squareoff_alert}"
            lines.append(tgt)
        lines.append(f"Reason: {self.reason}")
        lines.append(f"ID: {self.signal_id}")
        return "\n".join(lines)
