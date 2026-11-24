"""Time-zone helpers: IST, US Eastern time, and DST-aware MCX session hours.

MCX non-agri contracts close at 11:55 PM IST while the US is on standard time
(winter) and at 11:30 PM IST while the US is on daylight saving time (PLAN §1.1).
MCX switches on the first trading day after the US clock change, so the session
for an IST date is decided by whether New York is on DST at noon that day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")

MCX_OPEN = time(9, 0)
MCX_CLOSE_WINTER = time(23, 55)
MCX_CLOSE_SUMMER = time(23, 30)


def us_is_dst(d: date) -> bool:
    noon_et = datetime.combine(d, time(12, 0), tzinfo=ET)
    return bool(noon_et.dst())


def et_to_ist(d: date, t: time) -> datetime:
    """Convert a US Eastern wall-clock date+time into an IST datetime (DST-aware)."""
    return datetime.combine(d, t, tzinfo=ET).astimezone(IST)


@dataclass(frozen=True)
class McxSession:
    day: date
    open: datetime
    close: datetime
    us_dst: bool

    def broker_squareoff(self, minutes_before_close: int) -> datetime:
        return self.close - timedelta(minutes=minutes_before_close)


def mcx_session(d: date) -> McxSession:
    """Non-agri MCX session for IST calendar date d (holidays not applied here)."""
    dst = us_is_dst(d)
    close_t = MCX_CLOSE_SUMMER if dst else MCX_CLOSE_WINTER
    return McxSession(
        day=d,
        open=datetime.combine(d, MCX_OPEN, tzinfo=IST),
        close=datetime.combine(d, close_t, tzinfo=IST),
        us_dst=dst,
    )


def now_ist() -> datetime:
    return datetime.now(IST)
