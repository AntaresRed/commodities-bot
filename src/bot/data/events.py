"""US event calendar (EIA, FOMC, CPI, NFP) converted to IST, DST-aware (PLAN §3.2, §6).

Sources, fetched 24 Nov 2026:
  - EIA Weekly Petroleum Status Report schedule: https://www.eia.gov/petroleum/supply/weekly/schedule.php
  - EIA Weekly Natural Gas Storage Report schedule: https://ir.eia.gov/ngs/schedule.html
  - FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm (page updated 18 Nov 2026)
  - BLS CPI / Employment Situation: https://www.bls.gov/schedule/2026/home.htm,
    https://www.bls.gov/schedule/news_release/cpi.htm, .../empsit.htm (2027 page modified 9 Nov 2026)

"verified" column in the CSV:
  yes        date and time listed explicitly on the official page
  rule       weekly EIA release generated from the standard rule (Wed/Thu 10:30 ET),
             with every holiday exception the official page lists applied
  check      NOT verified: weekly EIA release in a week with a US federal holiday
             where the official page lists no change. The natural-gas page seems
             to list only upcoming exceptions, so past 2026 holiday weeks may have
             moved. Confirm before relying on these rows.
  tentative  FOMC 2027: "Each meeting date is tentative until confirmed at the
             meeting immediately preceding it" (Fed page)

Run `python -m bot.data.events` to regenerate config/events_<year>.csv.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from bot import paths
from bot.timeutil import IST, et_to_ist, us_is_dst

EIA_WPSR_URL = "https://www.eia.gov/petroleum/supply/weekly/schedule.php"
EIA_NGS_URL = "https://ir.eia.gov/ngs/schedule.html"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_URL = "https://www.bls.gov/schedule/{year}/home.htm"

T_1030 = time(10, 30)
T_1200 = time(12, 0)
T_0830 = time(8, 30)
T_1400 = time(14, 0)

# Normal release date -> (actual date, ET time, holiday). From the EIA pages.
EIA_WPSR_EXCEPTIONS: dict[date, tuple[date, time, str]] = {
    date(2026, 1, 21): (date(2026, 1, 22), T_1200, "Martin Luther King Jr. Day"),
    date(2026, 2, 18): (date(2026, 2, 19), T_1200, "Presidents' Day"),
    date(2026, 5, 27): (date(2026, 5, 28), T_1200, "Memorial Day"),
    date(2026, 9, 9): (date(2026, 9, 10), T_1200, "Labor Day"),
    date(2026, 10, 14): (date(2026, 10, 15), T_1200, "Columbus Day"),
    date(2026, 11, 11): (date(2026, 11, 12), T_1200, "Veterans Day"),
    date(2027, 1, 20): (date(2027, 1, 21), T_1200, "Martin Luther King Jr. Day"),
    date(2027, 2, 17): (date(2027, 2, 18), T_1200, "Presidents' Day"),
    date(2027, 6, 2): (date(2027, 6, 3), T_1200, "Memorial Day"),
    date(2027, 7, 7): (date(2027, 7, 8), T_1200, "Independence Day"),
    date(2027, 9, 8): (date(2027, 9, 9), T_1200, "Labor Day"),
    date(2027, 10, 13): (date(2027, 10, 14), T_1200, "Columbus Day"),
}

EIA_NGS_EXCEPTIONS: dict[date, tuple[date, time, str]] = {
    date(2026, 11, 12): (date(2026, 11, 13), T_1030, "Veterans Day"),
    date(2026, 11, 26): (date(2026, 11, 25), T_1200, "Thanksgiving Day"),
    date(2027, 11, 11): (date(2027, 11, 10), T_1200, "Veterans Day"),
    date(2027, 11, 25): (date(2027, 11, 24), T_1200, "Thanksgiving Day"),
}

# FOMC statement day (second day of each meeting), 2:00 PM ET.
FOMC_DATES: dict[int, list[tuple[date, str]]] = {
    2026: [
        (date(2026, 1, 28), "Jan 27-28"),
        (date(2026, 3, 18), "Mar 17-18, with SEP"),
        (date(2026, 4, 29), "Apr 28-29"),
        (date(2026, 6, 17), "Jun 16-17, with SEP"),
        (date(2026, 7, 29), "Jul 28-29"),
        (date(2026, 9, 16), "Sep 15-16, with SEP"),
        (date(2026, 10, 28), "Oct 27-28"),
        (date(2026, 12, 9), "Dec 8-9, with SEP"),
    ],
    2027: [
        (date(2027, 1, 27), "Jan 26-27"),
        (date(2027, 3, 17), "Mar 16-17"),
        (date(2027, 4, 28), "Apr 27-28"),
        (date(2027, 6, 9), "Jun 8-9"),
        (date(2027, 7, 28), "Jul 27-28"),
        (date(2027, 9, 15), "Sep 14-15"),
        (date(2027, 10, 27), "Oct 26-27, Monetary Policy Strategy Meeting"),
        (date(2027, 11, 17), "Nov 16-17"),
    ],
}

# BLS release date -> reference month. 8:30 AM ET.
CPI_DATES: dict[int, list[tuple[date, str]]] = {
    2026: [(date(2026, 1, 13), "Dec 2025"), (date(2026, 2, 13), "Jan 2026"), (date(2026, 3, 11), "Feb 2026"),
           (date(2026, 4, 10), "Mar 2026"), (date(2026, 5, 12), "Apr 2026"), (date(2026, 6, 10), "May 2026"),
           (date(2026, 7, 14), "Jun 2026"), (date(2026, 8, 12), "Jul 2026"), (date(2026, 9, 11), "Aug 2026"),
           (date(2026, 10, 14), "Sep 2026"), (date(2026, 11, 10), "Oct 2026"), (date(2026, 12, 10), "Nov 2026")],
    2027: [(date(2027, 1, 13), "Dec 2026"), (date(2027, 2, 11), "Jan 2027"), (date(2027, 3, 10), "Feb 2027"),
           (date(2027, 4, 13), "Mar 2027"), (date(2027, 5, 12), "Apr 2027"), (date(2027, 6, 10), "May 2027"),
           (date(2027, 7, 14), "Jun 2027"), (date(2027, 8, 11), "Jul 2027"), (date(2027, 9, 14), "Aug 2027"),
           (date(2027, 10, 14), "Sep 2027"), (date(2027, 11, 10), "Oct 2027"), (date(2027, 12, 10), "Nov 2027")],
}

NFP_DATES: dict[int, list[tuple[date, str]]] = {
    2026: [(date(2026, 1, 9), "Dec 2025"), (date(2026, 2, 11), "Jan 2026"), (date(2026, 3, 6), "Feb 2026"),
           (date(2026, 4, 3), "Mar 2026"), (date(2026, 5, 8), "Apr 2026"), (date(2026, 6, 5), "May 2026"),
           (date(2026, 7, 2), "Jun 2026"), (date(2026, 8, 7), "Jul 2026"), (date(2026, 9, 4), "Aug 2026"),
           (date(2026, 10, 2), "Sep 2026"), (date(2026, 11, 6), "Oct 2026"), (date(2026, 12, 4), "Nov 2026")],
    2027: [(date(2027, 1, 8), "Dec 2026"), (date(2027, 2, 5), "Jan 2027"), (date(2027, 3, 5), "Feb 2027"),
           (date(2027, 4, 2), "Mar 2027"), (date(2027, 5, 7), "Apr 2027"), (date(2027, 6, 4), "May 2027"),
           (date(2027, 7, 2), "Jun 2027"), (date(2027, 8, 6), "Jul 2027"), (date(2027, 9, 3), "Aug 2027"),
           (date(2027, 10, 8), "Sep 2027"), (date(2027, 11, 5), "Oct 2027"), (date(2027, 12, 3), "Nov 2027")],
}

SUPPORTED_YEARS = (2026, 2027)

CSV_COLUMNS = ["event", "markets", "date_et", "time_et", "datetime_ist", "date_ist", "time_ist",
               "us_dst", "verified", "note", "source"]


@dataclass(frozen=True)
class Event:
    event: str          # EIA_CRUDE, EIA_NATGAS, CPI, NFP, FOMC
    markets: str
    date_et: date
    time_et: time
    verified: str
    note: str
    source: str

    @property
    def ist(self) -> datetime:
        return et_to_ist(self.date_et, self.time_et)

    def row(self) -> dict[str, str]:
        ist = self.ist
        return {
            "event": self.event,
            "markets": self.markets,
            "date_et": self.date_et.isoformat(),
            "time_et": self.time_et.strftime("%H:%M"),
            "datetime_ist": ist.isoformat(timespec="minutes"),
            "date_ist": ist.date().isoformat(),
            "time_ist": ist.strftime("%H:%M"),
            "us_dst": "yes" if us_is_dst(self.date_et) else "no",
            "verified": self.verified,
            "note": self.note,
            "source": self.source,
        }


def _weekdays(year: int, weekday: int) -> list[date]:
    d = date(year, 1, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    out = []
    while d.year == year:
        out.append(d)
        d += timedelta(days=7)
    return out


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th given weekday of a month (n=-1 for the last one)."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_federal_holidays(year: int) -> dict[date, str]:
    """Observed US federal holidays (used only to flag weekly EIA rows to double-check)."""
    h = {
        _observed(date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        _observed(date(year, 6, 19)): "Juneteenth",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 10, 0, 2): "Columbus Day",
        _observed(date(year, 11, 11)): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        _observed(date(year, 12, 25)): "Christmas Day",
    }
    # New Year's Day of next year observed on Fri 31 Dec of this year
    nxt = _observed(date(year + 1, 1, 1))
    if nxt.year == year:
        h[nxt] = "New Year's Day (observed)"
    return h


def _holiday_near(release: date, holidays: dict[date, str]) -> str | None:
    """A holiday from Monday of the release week up to the day after the release."""
    monday = release - timedelta(days=release.weekday())
    d = monday
    while d <= release + timedelta(days=1):
        if d in holidays:
            return holidays[d]
        d += timedelta(days=1)
    return None


def _weekly(year: int, weekday: int, name: str, markets: str,
            exceptions: dict[date, tuple[date, time, str]], source: str) -> list[Event]:
    holidays = us_federal_holidays(year) | us_federal_holidays(year - 1)
    events = []
    for normal in _weekdays(year, weekday):
        if normal in exceptions:
            actual, t, holiday = exceptions[normal]
            events.append(Event(name, markets, actual, t, "yes",
                                f"moved from {normal.isoformat()} ({holiday})", source))
            continue
        holiday = _holiday_near(normal, holidays)
        if holiday:
            events.append(Event(name, markets, normal, T_1030, "check",
                                f"US holiday this week ({holiday}); official page lists no change - confirm",
                                source))
        else:
            events.append(Event(name, markets, normal, T_1030, "rule", "standard weekly release", source))
    return events


def build_events(year: int) -> list[Event]:
    if year not in SUPPORTED_YEARS:
        raise ValueError(f"No official schedule loaded for {year}; add it to bot/data/events.py")
    bls = BLS_URL.format(year=year)
    ev: list[Event] = []
    ev += _weekly(year, 2, "EIA_CRUDE", "crude", EIA_WPSR_EXCEPTIONS, EIA_WPSR_URL)
    ev += _weekly(year, 3, "EIA_NATGAS", "natgas", EIA_NGS_EXCEPTIONS, EIA_NGS_URL)
    ev += [Event("CPI", "all", d, T_0830, "yes", f"CPI for {m}", bls) for d, m in CPI_DATES[year]]
    ev += [Event("NFP", "all", d, T_0830, "yes", f"Employment Situation for {m}", bls) for d, m in NFP_DATES[year]]
    fomc_verified = "tentative" if year >= 2027 else "yes"
    ev += [Event("FOMC", "all", d, T_1400, fomc_verified,
                 f"FOMC decision ({label}); after MCX close: overnight gap risk", FOMC_URL)
           for d, label in FOMC_DATES[year]]
    ev.sort(key=lambda e: (e.ist, e.event))
    return ev


def write_events_csv(year: int, path: Path | None = None) -> Path:
    path = path or paths.CONFIG_DIR / f"events_{year}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for e in build_events(year):
            w.writerow(e.row())
    return path


def load_events(path: Path):
    """Load an events CSV as a DataFrame with a tz-aware IST 'datetime_ist' column."""
    import pandas as pd

    df = pd.read_csv(path, dtype=str)
    missing = set(CSV_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    df["datetime_ist"] = pd.to_datetime(df["datetime_ist"]).dt.tz_convert(IST)
    return df


def events_on(df, day: date):
    """Events whose IST date is `day` (for the morning message)."""
    return df[df["datetime_ist"].dt.date == day]


def main() -> None:
    for y in SUPPORTED_YEARS:
        p = write_events_csv(y)
        print(f"wrote {p} ({len(build_events(y))} events)")


if __name__ == "__main__":
    main()
