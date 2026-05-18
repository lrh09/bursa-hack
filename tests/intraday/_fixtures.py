"""Synthetic bar fixtures for intraday engine + strategy tests.

KL session = 09:00..16:59 with a phantom break 12:30..14:30. Real bars
total 360 per session (210 morning + 150 afternoon). Timestamps in fixtures
are UTC-naive, KL offset -8h (so 09:00 KL = 01:00 UTC).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl

KL_OFFSET = timedelta(hours=8)


def kl_to_utc(d: date, t: time) -> datetime:
    return datetime.combine(d, t) - KL_OFFSET


def synthetic_session(
    d: date,
    code: str = "ABC",
    base_price: float = 10.0,
    *,
    price_fn=None,
) -> pl.DataFrame:
    """One real KL session of 1-minute bars (360 bars, no phantom rows).

    `price_fn(i)` returns (open, high, low, close, volume, value) for minute-
    index i in [0, 360). Default = flat at `base_price`.
    """
    rows: list[dict] = []
    minute_idx = 0
    morning_end_kl = 12 * 60 + 30
    afternoon_start_kl = 14 * 60 + 30
    session_close_kl = 16 * 60 + 59  # last bar 16:59

    cursor_kl = 9 * 60  # 09:00
    while cursor_kl <= session_close_kl:
        if morning_end_kl <= cursor_kl < afternoon_start_kl:
            cursor_kl += 1
            continue
        kl_h, kl_m = divmod(cursor_kl, 60)
        ts = kl_to_utc(d, time(kl_h, kl_m))
        if price_fn is None:
            o = h = l = c = base_price
            v = 1000
            val = v * c
        else:
            o, h, l, c, v, val = price_fn(minute_idx)
        rows.append({
            "ts": ts,
            "code": code,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": int(v),
            "value": float(val),
        })
        minute_idx += 1
        cursor_kl += 1

    return pl.DataFrame(rows, schema={
        "ts": pl.Datetime("ns"),
        "code": pl.Utf8,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Int64,
        "value": pl.Float64,
    })


def orb_golden_session(d: date, code: str = "GOLD") -> pl.DataFrame:
    """A handcrafted 1-day OHLC sequence for the ORB golden test.

    Layout (5-min OR, long-only breakout, 1% stop, exit at close):
      minute 0..4 (09:00..09:04 KL)  : OR window. high=10.10, low=10.00.
      minute 5    (09:05 KL)         : Breakout bar -- high=10.20, close=10.15.
                                       Signal fires HERE.
      minute 6    (09:06 KL)         : Entry bar -- open=10.11. Engine fills here.
      minute 7..359                  : Drift up, NEVER hit stop (10.11 * 0.99 = 10.009).
                                       Last close=10.30.
    Expectation:
      - exactly 1 long entry at 10.11
      - no stop hit
      - exit at session_close (close of minute 359 = 10.30)
      - gross_ret = (10.30 - 10.11) / 10.11 ~ 0.01879
    """
    def price(i: int):
        if i == 0:    return (10.00, 10.05, 10.00, 10.05, 1000, 10050)
        if i == 1:    return (10.05, 10.08, 10.02, 10.07, 1000, 10070)
        if i == 2:    return (10.07, 10.09, 10.04, 10.08, 1000, 10080)
        if i == 3:    return (10.08, 10.10, 10.06, 10.10, 1000, 10100)  # OR_high=10.10
        if i == 4:    return (10.10, 10.10, 10.05, 10.07, 1000, 10070)
        if i == 5:    return (10.10, 10.20, 10.10, 10.15, 1500, 15225)  # breakout
        if i == 6:    return (10.11, 10.18, 10.10, 10.16, 1000, 10160)  # entry bar
        # minutes 7..359: drift up linearly to 10.30 at the end.
        # Linear from 10.16 at i=6 to 10.30 at i=359.
        if i >= 7:
            t = (i - 6) / (359 - 6)
            p = 10.16 + t * (10.30 - 10.16)
            # Stay above 10.01 always so the 1% stop never trips.
            return (p, p + 0.005, max(p - 0.005, 10.04), p, 1000, 1000 * p)
        return (10.0, 10.0, 10.0, 10.0, 1000, 10000)

    return synthetic_session(d, code=code, price_fn=price)


def narrow_or_session(d: date, code: str = "FLAT") -> pl.DataFrame:
    """OR range << 2%: high=10.001, low=10.000 (range = 0.01%). Used by
    min_or_range_pct test to verify gating drops the session."""
    def price(i: int):
        if i < 5:
            return (10.000, 10.001, 10.000, 10.000, 1000, 10000)
        # tiny drift, but high > 10.001 to trigger a breakout if not gated
        return (10.001, 10.010, 10.001, 10.005, 1000, 10005)

    return synthetic_session(d, code=code, price_fn=price)
