from datetime import date, time

import pandas as pd
import pytest

from bot import paths
from bot.data import events
from bot.timeutil import et_to_ist, mcx_session, us_is_dst


def test_us_dst_boundaries_2026():
    assert not us_is_dst(date(2026, 3, 6))
    assert us_is_dst(date(2026, 3, 9))      # DST began Sun 8 Mar 2026
    assert us_is_dst(date(2026, 10, 30))
    assert not us_is_dst(date(2026, 11, 2))  # DST ended Sun 1 Nov 2026


def test_mcx_close_follows_us_dst():
    # PLAN §1.1: winter schedule (11:55 PM) since Mon 2 Nov 2026
    assert mcx_session(date(2026, 11, 2)).close.time() == time(23, 55)
    assert mcx_session(date(2026, 10, 30)).close.time() == time(23, 30)
    s = mcx_session(date(2026, 11, 24))
    assert s.broker_squareoff(25).time() == time(23, 30)


def test_et_to_ist_matches_plan_table():
    # PLAN §3.2: EIA 10:30 ET = 9:00 PM IST winter, 8:00 PM summer; CPI 8:30 ET = 7:00/6:00 PM
    assert et_to_ist(date(2026, 11, 25), time(10, 30)).strftime("%H:%M") == "21:00"
    assert et_to_ist(date(2026, 7, 8), time(10, 30)).strftime("%H:%M") == "20:00"
    assert et_to_ist(date(2026, 12, 10), time(8, 30)).strftime("%H:%M") == "19:00"
    assert et_to_ist(date(2026, 6, 10), time(8, 30)).strftime("%H:%M") == "18:00"
    # FOMC 2 PM ET in winter lands after midnight IST, i.e. after the MCX close
    fomc = et_to_ist(date(2026, 12, 9), time(14, 0))
    assert (fomc.date(), fomc.strftime("%H:%M")) == (date(2026, 12, 10), "00:30")


@pytest.mark.parametrize("year", events.SUPPORTED_YEARS)
def test_event_counts_and_types(year):
    ev = events.build_events(year)
    kinds = pd.Series([e.event for e in ev]).value_counts()
    assert kinds["CPI"] == 12 and kinds["NFP"] == 12 and kinds["FOMC"] == 8
    assert 52 <= kinds["EIA_CRUDE"] <= 53 and 52 <= kinds["EIA_NATGAS"] <= 53
    assert all(e.verified in {"yes", "rule", "check", "tentative"} for e in ev)


def test_eia_holiday_exceptions_applied():
    ev = {(e.event, e.date_et): e for e in events.build_events(2026)}
    moved = ev[("EIA_CRUDE", date(2026, 11, 12))]          # Veterans Day: Wed -> Thu 12:00 ET
    assert moved.time_et == time(12, 0) and moved.verified == "yes"
    assert ("EIA_CRUDE", date(2026, 11, 11)) not in ev
    tg = ev[("EIA_NATGAS", date(2026, 11, 25))]            # Thanksgiving: Thu -> Wed 12:00 ET
    assert tg.row()["time_ist"] == "22:30"
    assert ev[("EIA_NATGAS", date(2026, 1, 1))].verified == "check"   # New Year's Day: flagged, not assumed


def test_fomc_2027_marked_tentative():
    f = [e for e in events.build_events(2027) if e.event == "FOMC"]
    assert {e.verified for e in f} == {"tentative"}


@pytest.mark.parametrize("year", events.SUPPORTED_YEARS)
def test_shipped_csv_matches_generator(year):
    df = events.load_events(paths.CONFIG_DIR / f"events_{year}.csv")
    assert len(df) == len(events.build_events(year))
    assert str(df["datetime_ist"].dt.tz) == "Asia/Kolkata"
    day = events.events_on(df, date(year, 1, 13) if year == 2026 else date(2027, 1, 13))
    assert "CPI" in set(day["event"])


def test_unsupported_year():
    with pytest.raises(ValueError):
        events.build_events(2030)


def test_federal_holidays_2026():
    h = events.us_federal_holidays(2026)
    assert h[date(2026, 11, 26)] == "Thanksgiving Day"
    assert h[date(2026, 7, 3)] == "Independence Day"      # 4 Jul 2026 is a Saturday
    assert date(2026, 1, 19) in h                          # MLK day
