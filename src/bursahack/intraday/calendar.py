"""Exchange calendar facade — XKLS-aware, KL <-> UTC conversion, phantom-bar mask.

Scalability choice: this module is the SINGLE source of truth for "what is a
session" and "what is a tradable minute" across every downstream module. The
loader, universe, engine, and diagnostics all go through this facade, so adding
SGX/HKEX/etc. later means adding one more `ExchangeCalendar(name="XSES")` and
nothing else changes.

Data is stored in UTC in parquet (timezone-naive datetime64[ns] but representing
UTC). The calendar handles KL <-> UTC translation. Kuala Lumpur is UTC+8 with
no DST.

Bursa Malaysia trading hours (XKLS):
    Morning session : 09:00 - 12:30 KL
    Lunch break     : 12:30 - 14:30 KL  (stored as ZERO-VOL filler bars)
    Afternoon       : 14:30 - 17:00 KL  (data shows 14:30 - 16:59)

The `exchange_calendars` PyPI package (>=4.5) provides XKLS sessions but does
NOT model the midday break (open_times=09:00, close_times=17:00, no
break_start_times). We layer the break on top here.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

import exchange_calendars as _ec
import pandas as pd
import polars as pl

KL_OFFSET_HOURS: int = 8  # KL is UTC+8, no DST
KL_TZ = timezone(timedelta(hours=KL_OFFSET_HOURS))

# Break window in KL local time
_BREAK_START_KL = time(12, 30)
_BREAK_END_KL = time(14, 30)


def kl_to_utc(ts: datetime) -> datetime:
    """Convert a naive KL-local datetime to a naive UTC datetime."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KL_TZ)
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_kl(ts: datetime) -> datetime:
    """Convert a naive UTC datetime to a naive KL-local datetime."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(KL_TZ).replace(tzinfo=None)


class ExchangeCalendar:
    """Thin facade over `exchange_calendars` with KL phantom-break logic.

    The constructor accepts an exchange code (e.g. "XKLS"). If the code isn't
    in `exchange_calendars`, raises ValueError; in that situation, swap in a
    purpose-built `BursaCalendar` subclass via the registry below.
    """

    def __init__(self, name: str = "XKLS") -> None:
        self.name = name
        try:
            self._ec = _ec.get_calendar(name)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"exchange code {name!r} not found in `exchange_calendars`. "
                "Implement a custom calendar with the same facade."
            ) from exc

    # ------------------------------ sessions ------------------------------

    def sessions(self, start: date, end: date) -> list[date]:
        """Return ordered list of session dates in [start, end] (inclusive)."""
        idx = self._ec.sessions_in_range(
            pd.Timestamp(start), pd.Timestamp(end)
        )
        return [ts.date() for ts in idx]

    def is_session(self, d: date) -> bool:
        """True iff `d` is a trading session on this exchange."""
        return bool(self._ec.is_session(pd.Timestamp(d)))

    # ------------------------------ minutes ------------------------------

    def phantom_bar_mask(self, ts_series: pl.Series) -> pl.Series:
        """Return a boolean mask True for timestamps inside the phantom break.

        Phantom break = 12:30 KL <= ts < 14:30 KL, on every session day.
        Inputs are UTC-naive datetime64; we translate to KL by adding offset.

        This is bar-resolution agnostic — works for 1m, 5m, etc. timestamps.
        Note that a 14:30 bar is NOT phantom (it's the first afternoon bar).
        """
        if ts_series.dtype != pl.Datetime:
            raise TypeError(f"phantom_bar_mask expects Datetime series, got {ts_series.dtype}")
        # KL local minutes-of-day. Important: dt.hour/dt.minute return u8 which
        # overflows on *60; cast to Int32 before multiplying.
        # Working in pure-expression form so it returns a Series when given one.
        df = pl.DataFrame({"ts": ts_series})
        out = df.select([
            (
                ((pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.hour().cast(pl.Int32) * 60
                 + (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.minute().cast(pl.Int32))
                .alias("mins")
            )
        ])
        mins = out.get_column("mins")
        start = _BREAK_START_KL.hour * 60 + _BREAK_START_KL.minute  # 750
        end = _BREAK_END_KL.hour * 60 + _BREAK_END_KL.minute        # 870
        return (mins >= start) & (mins < end)

    def session_minutes_count(self) -> int:
        """How many real (non-phantom) tradable minutes per session.

        09:00-12:30 = 210 min ; 14:30-17:00 = 150 min ; total = 360 minutes.
        The dataset shows last bar at 16:59 so 09:00-16:59 inclusive = 480
        bars before filtering, 360 after.
        """
        return 360

    # ------------------------------ holidays ------------------------------

    def holidays(self, start: date, end: date) -> list[date]:
        """Weekdays in [start, end] that are NOT sessions (i.e. exchange-closed)."""
        all_wd = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))
        sess = set(self.sessions(start, end))
        return [d.date() for d in all_wd if d.date() not in sess]

    # ----------------------------- iteration -----------------------------

    def first_session_of_each_month(
        self, start: date, end: date
    ) -> list[date]:
        """Return the first session date of every calendar month in [start, end]."""
        sess = self.sessions(start, end)
        out: list[date] = []
        seen: set[tuple[int, int]] = set()
        for d in sess:
            key = (d.year, d.month)
            if key not in seen:
                seen.add(key)
                out.append(d)
        return out


__all__ = [
    "ExchangeCalendar",
    "KL_OFFSET_HOURS",
    "KL_TZ",
    "kl_to_utc",
    "utc_to_kl",
]
