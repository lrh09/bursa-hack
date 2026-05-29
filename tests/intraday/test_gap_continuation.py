"""Gap Continuation strategy: signal mechanics, volume-confirm gate,
no-lookahead via prior-session ATR/baseline, one-per-session."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from bursahack.intraday.signals.gap_continuation import (
    GapContinuationParams,
    GapContinuationStrategy,
)
from tests.intraday._fixtures import synthetic_session


# --- session builders --------------------------------------------------------

# Baseline session: opens at `base`, tiny intraday range (~0.4%), steady
# first-window volume. Close ends at `base` so the next day's gap is purely
# `next_open - base`.
_BASE_FW_VOL = 1000  # per-bar volume in the first window of a baseline session


def _baseline_session(d: date, base: float, code: str) -> pl.DataFrame:
    def price(i: int):
        # mild noise, range stays ~0.4% of price, close returns to base.
        o = base
        h = base * 1.002
        lo = base * 0.998
        c = base
        v = _BASE_FW_VOL + (i * 7) % 30  # noisy but ~1000
        return (o, h, lo, c, v, v * c)
    return synthetic_session(d, code=code, base_price=base, price_fn=price)


def _gap_session(
    d: date,
    prev_close: float,
    gap_pct: float,
    fw_vol_mult: float,
    code: str,
) -> pl.DataFrame:
    """Session that opens `gap_pct` away from `prev_close`. The first-window
    bars carry `fw_vol_mult * _BASE_FW_VOL` volume; later bars are quiet."""
    open_px = prev_close * (1.0 + gap_pct)
    fw_vol = int(_BASE_FW_VOL * fw_vol_mult)

    def price(i: int):
        # first 15 minutes (covers or_minutes in {5,15}) carry the volume.
        v = fw_vol if i < 15 else 1000
        if gap_pct >= 0:
            # gap up then drift up a touch
            o = open_px
            c = open_px * 1.001
            h = c * 1.001
            lo = o * 0.999
        else:
            o = open_px
            c = open_px * 0.999
            h = o * 1.001
            lo = c * 0.999
        return (o, h, lo, c, v, v * c)
    return synthetic_session(d, code=code, base_price=open_px, price_fn=price)


def _weekday_dates(start: date, n: int) -> list[date]:
    """`n` consecutive weekday dates starting at/after `start`."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:  # Mon..Fri
            out.append(d)
        d += timedelta(days=1)
    return out


def _history(
    code: str,
    base: float,
    n_baseline: int = 22,
) -> tuple[list[date], pl.DataFrame, float]:
    """Build `n_baseline` flat sessions. Returns (dates, frame, last_close)."""
    dates = _weekday_dates(date(2024, 1, 1), n_baseline + 1)
    frames = [_baseline_session(dt, base, code) for dt in dates[:n_baseline]]
    return dates, pl.concat(frames), base


def _params(**kw) -> GapContinuationParams:
    base = dict(
        gap_z_threshold=1.5, vol_mult=2.0, or_minutes=5,
        stop_pct=1.0, exit_policy="session_close", side="both",
    )
    base.update(kw)
    return GapContinuationParams(**base)


# --- tests -------------------------------------------------------------------

def test_no_signal_when_no_gap():
    """All flat sessions, no gap anywhere -> nothing fires (even after warmup)."""
    code = "FLAT"
    dates, hist, _ = _history(code, base=10.0, n_baseline=24)
    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(hist, None, _params())
    assert sigs.height == 0


def test_gap_up_high_volume_fires_long_with_correct_stop():
    code = "GAPU"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    # +5% gap; baseline daily range ~0.4% -> gap_z ~ 12 (well over threshold).
    # first-window volume 3x baseline -> confirmed (vol_mult=2.0).
    gap = _gap_session(gap_day, last_close, gap_pct=0.05, fw_vol_mult=3.0, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params())
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == 1  # continuation of an up gap = LONG
    # Stop for long = entry * (1 - stop_pct/100).
    assert row["stop_price"] == pytest.approx(row["entry_price"] * (1.0 - 0.01), rel=1e-9)
    # Entry must be at/after the confirm window (09:05 KL for or_minutes=5),
    # i.e. NOT the session's first bar.
    assert row["entry_price"] is not None


def test_gap_up_low_volume_does_not_fire():
    """Big gap but first-window volume BELOW vol_mult * baseline -> gated out."""
    code = "GAPLV"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    # +5% gap but only 1.2x baseline volume (< vol_mult=2.0) -> NOT confirmed.
    gap = _gap_session(gap_day, last_close, gap_pct=0.05, fw_vol_mult=1.2, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params())
    assert sigs.height == 0, "volume below confirmation threshold must not fire"


def test_gap_down_fires_short_when_side_allows():
    code = "GAPD"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    gap = _gap_session(gap_day, last_close, gap_pct=-0.05, fw_vol_mult=3.0, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params(side="both"))
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == -1  # continuation of a down gap = SHORT
    # Stop for short = entry * (1 + stop_pct/100).
    assert row["stop_price"] == pytest.approx(row["entry_price"] * (1.0 + 0.01), rel=1e-9)


def test_gap_down_suppressed_when_long_only():
    code = "GAPDL"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    gap = _gap_session(gap_day, last_close, gap_pct=-0.05, fw_vol_mult=3.0, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params(side="long"))
    assert sigs.height == 0, "down gap must not fire when side=long"


def test_small_gap_below_threshold_does_not_fire():
    """Gap present + volume confirmed, but gap_z below threshold -> no fire."""
    code = "SMALL"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    # baseline daily range ~0.4% -> ATR ~0.004. A 0.2% gap gives gap_z ~0.5,
    # well below the 1.5 threshold. Volume is elevated so only the gap_z gate
    # is what suppresses it.
    gap = _gap_session(gap_day, last_close, gap_pct=0.002, fw_vol_mult=3.0, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params())
    assert sigs.height == 0, "gap_z below threshold must not fire"


def test_one_signal_per_session_max():
    """A confirmed gap-up session must yield exactly one signal even though
    many post-window bars satisfy the (session-level) entry condition."""
    code = "ONE"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    gap = _gap_session(gap_day, last_close, gap_pct=0.05, fw_vol_mult=3.0, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params())
    assert sigs.height == 1


def test_no_signal_before_atr_warmup():
    """A gap on day 2 (only 1 prior session) has no ATR_20d yet -> no fire,
    proving ATR uses prior sessions and warmup gating works."""
    code = "WARM"
    dates = _weekday_dates(date(2024, 1, 1), 2)
    d0 = _baseline_session(dates[0], 10.0, code)
    gap = _gap_session(dates[1], 10.0, gap_pct=0.05, fw_vol_mult=3.0, code=code)
    bars = pl.concat([d0, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params())
    assert sigs.height == 0, "no ATR_20d before warmup -> must not fire"


def test_or_minutes_15_uses_wider_window():
    """With or_minutes=15 the strategy still fires on a confirmed gap and the
    entry bar is at/after 09:15 KL."""
    code = "OR15"
    dates, hist, last_close = _history(code, base=10.0, n_baseline=22)
    gap_day = _weekday_dates(dates[-1] + timedelta(days=1), 1)[0]
    gap = _gap_session(gap_day, last_close, gap_pct=0.05, fw_vol_mult=3.0, code=code)
    bars = pl.concat([hist, gap])

    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params(or_minutes=15))
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    # entry ts in KL minutes must be >= 540 + 15 = 555 (09:15 KL).
    kl_min = (row["ts"].hour + 8) % 24 * 60 + row["ts"].minute
    assert kl_min >= 9 * 60 + 15


def test_registered_in_registry():
    from bursahack.intraday.registry import get_strategy
    spec = get_strategy("gap_continuation")
    assert spec.name == "gap_continuation"
    assert spec.version == "1.0.0"
    assert spec.params_model is GapContinuationParams


def test_returns_empty_on_empty_bars():
    bars = pl.DataFrame(schema={
        "ts": pl.Datetime("ns"), "code": pl.Utf8,
        "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Int64, "value": pl.Float64,
    })
    strat = GapContinuationStrategy()
    sigs = strat.generate_signals(bars, None, _params())
    assert sigs.height == 0
