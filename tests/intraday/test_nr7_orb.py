"""NR7-conditioned ORB: NR(N) flag correctness, arming, and ORB mechanics.

We build multi-day fixtures by concatenating per-session frames (one
`synthetic_session` call per KL date). The NR(N) flag is computed on the
per-(code, day) DAILY range; the ORB is armed only on the session AFTER an
NR(N) session.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from bursahack.intraday.signals.nr7_orb import NR7ORBParams, NR7ORBStrategy
from tests.intraday._fixtures import synthetic_session


# Business days; KL sessions are independent here so any distinct dates work.
_DATES = [
    date(2024, 1, 1),
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),  # index 6 -> engineered NR7 (narrowest)
    date(2024, 1, 10),  # index 7 -> armed; clean breakout
    date(2024, 1, 11),  # index 8 -> NOT armed (prior day 1/10 not NR7)
]


def _flat_day(d: date, code: str, day_range: float) -> pl.DataFrame:
    """A quiet session whose intraday range == `day_range`.

    The OR window (first 5 bars) spans [10.00, 10.00 + day_range] so the day's
    high/low equals exactly that band, and nothing breaks out afterward (we
    sit inside the OR band for the rest of the session). Used to control each
    session's DAILY range precisely for the NR(N) computation.
    """
    hi = 10.00 + day_range

    def price(i: int):
        if i < 5:
            # OR window: establish high=hi, low=10.00 across the 5 bars.
            if i == 0:
                return (10.00, hi, 10.00, 10.00, 1000, 10000)
            return (10.00, hi, 10.00, 10.00, 1000, 10000)
        # After OR: stay strictly inside (10.00, hi) -> no breakout.
        mid = 10.00 + day_range / 2.0
        return (mid, mid, mid, mid, 1000, 1000 * mid)

    return synthetic_session(d, code=code, price_fn=price)


def _breakout_day(d: date, code: str, day_range: float = 0.50) -> pl.DataFrame:
    """A session with a clean upside breakout after the OR window.

    OR window (first 5 bars): high=10.10, low=10.00 (OR range 0.10).
    Bar i=5: breakout, high=10.20, close=10.15 -> long signal here.
    Then drift up; never revisits OR. Daily range ends up ~0.30 (>> a narrow
    day) so this session is itself NOT an NR day.
    """
    def price(i: int):
        if i == 0:    return (10.00, 10.05, 10.00, 10.05, 1000, 10050)
        if i == 1:    return (10.05, 10.08, 10.02, 10.07, 1000, 10070)
        if i == 2:    return (10.07, 10.09, 10.04, 10.08, 1000, 10080)
        if i == 3:    return (10.08, 10.10, 10.06, 10.10, 1000, 10100)  # OR_high=10.10
        if i == 4:    return (10.10, 10.10, 10.05, 10.07, 1000, 10070)
        if i == 5:    return (10.10, 10.20, 10.10, 10.15, 1500, 15225)  # breakout
        if i == 6:    return (10.11, 10.18, 10.10, 10.16, 1000, 10160)
        # drift up to ~10.30, stay above OR band.
        t = (i - 6) / (359 - 6)
        p = 10.16 + t * (10.30 - 10.16)
        return (p, p + 0.005, max(p - 0.005, 10.12), p, 1000, 1000 * p)

    return synthetic_session(d, code=code, price_fn=price)


def _down_breakout_day(d: date, code: str) -> pl.DataFrame:
    """Clean DOWNSIDE breakout after the OR window (for the short-side test).

    OR window: high=10.10, low=10.00. Bar i=5: low=9.90 -> short signal.
    Then drift down.
    """
    def price(i: int):
        if i == 0:    return (10.05, 10.05, 10.00, 10.03, 1000, 10030)
        if i == 1:    return (10.03, 10.08, 10.02, 10.05, 1000, 10050)
        if i == 2:    return (10.05, 10.09, 10.04, 10.06, 1000, 10060)
        if i == 3:    return (10.06, 10.10, 10.05, 10.08, 1000, 10080)  # OR_high=10.10
        if i == 4:    return (10.08, 10.10, 10.00, 10.02, 1000, 10020)  # OR_low=10.00
        if i == 5:    return (10.00, 10.00, 9.90, 9.95, 1500, 14925)    # breakout DOWN
        if i == 6:    return (9.94, 9.96, 9.88, 9.90, 1000, 9900)
        t = (i - 6) / (359 - 6)
        p = 9.90 - t * (9.90 - 9.70)
        return (p, min(p + 0.005, 9.98), p - 0.005, p, 1000, 1000 * p)

    return synthetic_session(d, code=code, price_fn=price)


def _multiday(code: str = "NR7", *, nr_index: int = 6,
              ranges: list[float] | None = None,
              breakout_index: int = 7,
              down_breakout: bool = False) -> pl.DataFrame:
    """Concatenate 9 sessions.

    Default: sessions 0..5 have a descending-but-still-wide range, session
    `nr_index` (6) is engineered to be the narrowest (NR7), `breakout_index`
    (7, the day AFTER the NR day) has a clean breakout, session 8 is a quiet
    no-breakout day. Ranges chosen so ONLY session 6 is NR7.
    """
    if ranges is None:
        # Strictly descending so session 6 (0.10) is the min of any trailing
        # window ending at 6. Sessions 0..5 sit at 0.60..0.20; the breakout
        # day's own range (~0.30) and day-8's range are not narrower than 0.10.
        ranges = [0.60, 0.55, 0.50, 0.45, 0.40, 0.20, 0.10]

    frames: list[pl.DataFrame] = []
    for i, d in enumerate(_DATES):
        if i == breakout_index:
            if down_breakout:
                frames.append(_down_breakout_day(d, code))
            else:
                frames.append(_breakout_day(d, code))
        elif i <= nr_index:
            frames.append(_flat_day(d, code, ranges[i]))
        else:
            # Days after the breakout day: quiet, range 0.35 (not NR).
            frames.append(_flat_day(d, code, 0.35))
    return pl.concat(frames).sort(["code", "ts"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_nr7_flag_identifies_narrowest_of_7():
    """Session 6 (range 0.10) is strictly the narrowest of the trailing 7
    sessions [0..6]. It must be the ONLY armed-trigger NR day, so the ORB
    fires exactly on session 7 (the day after it)."""
    bars = _multiday()
    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=7, opening_range_minutes=5, side="both")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    # Exactly one signal, and it lands on the breakout session (1/10).
    assert sigs.height == 1
    sig_ts = sigs.get_column("ts")[0]
    sig_kl_date = (
        sigs.with_columns(
            (pl.col("ts") + pl.duration(hours=8)).dt.date().alias("_d")
        ).get_column("_d")[0]
    )
    assert sig_kl_date == date(2024, 1, 10)


def test_no_signal_when_prior_day_not_nr7():
    """Session 8 (1/11) has its OWN breakout but its prior day (1/10) is NOT
    an NR7 day -> must NOT fire. We engineer a breakout on day 8 and confirm
    only the genuine NR7-armed day (1/10) produces a signal, not day 8."""
    # Build the standard fixture but ALSO make day 8 a breakout day.
    code = "NR7B"
    ranges = [0.60, 0.55, 0.50, 0.45, 0.40, 0.20, 0.10]
    frames: list[pl.DataFrame] = []
    for i, d in enumerate(_DATES):
        if i == 7:
            frames.append(_breakout_day(d, code))   # armed -> should fire
        elif i == 8:
            frames.append(_breakout_day(d, code))   # NOT armed -> must NOT fire
        else:
            frames.append(_flat_day(d, code, ranges[i] if i <= 6 else 0.35))
    bars = pl.concat(frames).sort(["code", "ts"])

    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=7, opening_range_minutes=5, side="both")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)

    kl_dates = sigs.with_columns(
        (pl.col("ts") + pl.duration(hours=8)).dt.date().alias("_d")
    ).get_column("_d").to_list()
    assert date(2024, 1, 10) in kl_dates, "armed day (after NR7) should fire"
    assert date(2024, 1, 11) not in kl_dates, (
        "day whose prior session was NOT NR7 must not fire even with a breakout"
    )
    assert sigs.height == 1


def test_orb_fires_on_session_after_genuine_nr7():
    """Positive: the session immediately after a genuine NR7 day fires a
    long signal on the breakout bar (i=5 of that session)."""
    bars = _multiday()
    strat = NR7ORBStrategy()
    params = NR7ORBParams(
        nr_window=7, opening_range_minutes=5, stop_pct=1.0, side="long",
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == 1
    # Breakout bar close = 10.15; long stop = entry * (1 - 1%).
    assert row["entry_price"] == pytest.approx(10.15, abs=1e-6)
    assert row["stop_price"] == pytest.approx(10.15 * 0.99, rel=1e-9)


def test_long_stop_sign():
    bars = _multiday()
    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=7, opening_range_minutes=5,
                          stop_pct=2.0, side="long")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == 1
    # Long stop is BELOW entry.
    assert row["stop_price"] < row["entry_price"]
    assert row["stop_price"] == pytest.approx(row["entry_price"] * 0.98, rel=1e-9)


def test_short_stop_sign():
    bars = _multiday(code="NR7S", down_breakout=True)
    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=7, opening_range_minutes=5,
                          stop_pct=1.5, side="short")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == -1
    # Short stop is ABOVE entry.
    assert row["stop_price"] > row["entry_price"]
    assert row["stop_price"] == pytest.approx(row["entry_price"] * 1.015, rel=1e-9)


def test_one_signal_per_session_max():
    """Even with side='both' and a session that crosses both OR bounds, at
    most one signal per (code, day). The standard armed day has only an up
    breakout, so exactly one signal."""
    bars = _multiday()
    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=7, opening_range_minutes=5, side="both")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    # Group by (code, kl_date) and assert no group has >1 row.
    grp = sigs.with_columns(
        (pl.col("ts") + pl.duration(hours=8)).dt.date().alias("_d")
    ).group_by(["code", "_d"]).agg(pl.len().alias("n"))
    assert grp.get_column("n").max() == 1
    assert sigs.height == 1


def test_nr_window_4_variant():
    """nr_window=4 arms off the narrowest of the trailing 4. With our
    descending ranges, session 6 (0.10) is still the min of [3,4,5,6], so the
    day after (1/10) still fires."""
    bars = _multiday()
    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=4, opening_range_minutes=5, side="long")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    sig_kl_date = sigs.with_columns(
        (pl.col("ts") + pl.duration(hours=8)).dt.date().alias("_d")
    ).get_column("_d")[0]
    assert sig_kl_date == date(2024, 1, 10)


def test_no_lookahead_first_n_sessions_never_arm():
    """A window of N sessions is required before NR(N) is defined. The first
    N sessions can never be 'armed' (the prior-day NR flag is null/false), so
    a breakout occurring on session index < nr_window must not fire."""
    code = "EARLY"
    frames: list[pl.DataFrame] = []
    for i, d in enumerate(_DATES):
        if i == 2:
            # Breakout on the 3rd session — far too early for NR7 to be armed.
            frames.append(_breakout_day(d, code))
        else:
            frames.append(_flat_day(d, code, 0.30))
    bars = pl.concat(frames).sort(["code", "ts"])
    strat = NR7ORBStrategy()
    params = NR7ORBParams(nr_window=7, opening_range_minutes=5, side="both")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    early_dates = sigs.with_columns(
        (pl.col("ts") + pl.duration(hours=8)).dt.date().alias("_d")
    ).get_column("_d").to_list() if sigs.height else []
    assert date(2024, 1, 3) not in early_dates


def test_registered_in_registry():
    from bursahack.intraday.registry import get_strategy
    spec = get_strategy("nr7_orb")
    assert spec.name == "nr7_orb"
    assert spec.version == "1.0.0"
    assert spec.params_model is NR7ORBParams
    assert "NR7" in spec.description


def test_returns_empty_on_empty_bars():
    bars = pl.DataFrame(schema={
        "ts": pl.Datetime("ns"), "code": pl.Utf8,
        "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Int64, "value": pl.Float64,
    })
    strat = NR7ORBStrategy()
    sigs = strat.generate_signals(bars, universe_members=None,
                                  params=NR7ORBParams())
    assert sigs.height == 0
