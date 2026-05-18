"""ExchangeCalendar / XKLS smoke + phantom-bar mask."""
from __future__ import annotations

from datetime import date, datetime

import polars as pl

from bursahack.intraday.calendar import ExchangeCalendar


def test_xkls_session_count_2020_09_to_2021_10() -> None:
    cal = ExchangeCalendar("XKLS")
    sessions = cal.sessions(date(2020, 9, 24), date(2021, 10, 4))
    # Phase 1 audit: 253 sessions over the data window.
    assert len(sessions) == 253


def test_xkls_holiday_marked() -> None:
    cal = ExchangeCalendar("XKLS")
    # Maulidur Rasul 2020-10-29
    assert not cal.is_session(date(2020, 10, 29))
    # National Day 2021-08-31
    assert not cal.is_session(date(2021, 8, 31))
    # 2020-09-24 is a session
    assert cal.is_session(date(2020, 9, 24))


def test_phantom_bar_mask_kl_local() -> None:
    cal = ExchangeCalendar("XKLS")
    # ts stored as UTC -> KL is +8h
    # 09:35 KL = 01:35 UTC -> NOT phantom
    # 12:35 KL = 04:35 UTC -> phantom
    # 14:30 KL = 06:30 UTC -> NOT phantom (first afternoon bar)
    # 14:35 KL = 06:35 UTC -> NOT phantom
    s = pl.Series("ts", [
        datetime(2021, 4, 7, 1, 35),   # 09:35 KL Wed
        datetime(2021, 4, 7, 4, 35),   # 12:35 KL Wed
        datetime(2021, 4, 7, 6, 30),   # 14:30 KL Wed (first afternoon)
        datetime(2021, 4, 7, 6, 35),   # 14:35 KL Wed
        datetime(2021, 4, 7, 4, 30),   # 12:30 KL Wed (first phantom)
    ], dtype=pl.Datetime)
    mask = cal.phantom_bar_mask(s).to_list()
    assert mask == [False, True, False, False, True]


def test_first_session_of_each_month() -> None:
    cal = ExchangeCalendar("XKLS")
    firsts = cal.first_session_of_each_month(date(2020, 9, 24), date(2021, 10, 4))
    months = {(d.year, d.month) for d in firsts}
    # 14 distinct months over the window
    assert len(months) == 14
    # First of each month is a session
    for d in firsts:
        assert cal.is_session(d)
