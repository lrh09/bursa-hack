"""VWAP Reclaim directional strategy: signal mechanics + no-lookahead."""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from bursahack.intraday.signals.vwap_reclaim import (
    VWAPReclaimParams,
    VWAPReclaimStrategy,
)
from tests.intraday._fixtures import synthetic_session


# ---------------------------------------------------------------------------
# Session builders
# ---------------------------------------------------------------------------


def _flat_session(d: date, code: str = "FLT"):
    """Price pinned exactly on VWAP every bar (close == vwap) -> never crosses
    strictly above/below, so nothing fires."""
    def price(i: int):
        return (10.00, 10.00, 10.00, 10.00, 1000, 10_000.0)
    return synthetic_session(d, code=code, price_fn=price)


def _long_reclaim_session(
    d: date, below_for: int, reclaim_at: int, code: str = "LNG"
):
    """Open at VWAP, sit strictly below VWAP for `below_for` bars, then on bar
    `reclaim_at` jump the close well above the running VWAP -> long reclaim.

    Bar 0 establishes VWAP at exactly 10.00 (close==vwap, neither above nor
    below). Bars 1..below_for sit at close 9.90 (below the ~10.00 VWAP). Bar
    `reclaim_at == below_for + 1` closes at 10.40 (clearly above VWAP).
    """
    assert reclaim_at == below_for + 1
    def price(i: int):
        if i == 0:
            return (10.00, 10.00, 10.00, 10.00, 1000, 10_000.0)
        if 1 <= i <= below_for:
            return (9.90, 9.92, 9.88, 9.90, 1000, 9_900.0)
        if i == reclaim_at:
            return (10.40, 10.45, 10.35, 10.40, 1000, 10_400.0)
        # After the reclaim, drift around so no second cross matters.
        return (10.40, 10.42, 10.38, 10.40, 1000, 10_400.0)
    return synthetic_session(d, code=code, price_fn=price)


def _short_reclaim_session(
    d: date, above_for: int, reclaim_at: int, code: str = "SHT"
):
    """Mirror of the long case: sit strictly ABOVE VWAP for `above_for` bars,
    then cross below on bar `reclaim_at`."""
    assert reclaim_at == above_for + 1
    def price(i: int):
        if i == 0:
            return (10.00, 10.00, 10.00, 10.00, 1000, 10_000.0)
        if 1 <= i <= above_for:
            return (10.10, 10.12, 10.08, 10.10, 1000, 10_100.0)
        if i == reclaim_at:
            return (9.60, 9.65, 9.55, 9.60, 1000, 9_600.0)
        return (9.60, 9.62, 9.58, 9.60, 1000, 9_600.0)
    return synthetic_session(d, code=code, price_fn=price)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_signal_on_flat_session():
    bars = _flat_session(date(2024, 1, 8))
    strat = VWAPReclaimStrategy()
    sigs = strat.generate_signals(
        bars, universe_members=None, params=VWAPReclaimParams()
    )
    assert sigs.height == 0


def test_long_reclaim_fires_with_correct_stop_sign():
    # 15 bars below, reclaim on the 16th tracked bar (i == 16).
    bars = _long_reclaim_session(date(2024, 1, 8), below_for=15, reclaim_at=16)
    strat = VWAPReclaimStrategy()
    params = VWAPReclaimParams(min_below_minutes=15, stop_pct=1.0, side="long")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == 1
    # Long stop = entry * (1 - stop_pct/100); entry hint = close = 10.40.
    assert row["stop_price"] == pytest.approx(10.40 * 0.99, rel=1e-9)
    assert row["stop_price"] < row["entry_price"]


def test_short_reclaim_fires():
    bars = _short_reclaim_session(date(2024, 1, 8), above_for=15, reclaim_at=16)
    strat = VWAPReclaimStrategy()
    params = VWAPReclaimParams(min_below_minutes=15, stop_pct=1.0, side="short")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == -1
    # Short stop = entry * (1 + stop_pct/100); entry hint = close = 9.60.
    assert row["stop_price"] == pytest.approx(9.60 * 1.01, rel=1e-9)
    assert row["stop_price"] > row["entry_price"]


def test_no_fire_before_min_below_satisfied():
    """A reclaim that happens after only 10 below-bars must NOT fire when the
    threshold is 15 (no-lookahead / sustained-departure sanity)."""
    bars = _long_reclaim_session(date(2024, 1, 8), below_for=10, reclaim_at=11)
    strat = VWAPReclaimStrategy()
    params = VWAPReclaimParams(min_below_minutes=15, stop_pct=1.0, side="long")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 0, "reclaim before min_below_minutes should not fire"


def test_threshold_15_fires_when_exactly_15_below():
    """Same session as the negative test but with threshold lowered to match:
    a 15-bar departure with threshold 15 SHOULD fire. Guards against an
    off-by-one that would make the threshold unreachable."""
    bars = _long_reclaim_session(date(2024, 1, 8), below_for=15, reclaim_at=16)
    strat = VWAPReclaimStrategy()
    params = VWAPReclaimParams(min_below_minutes=15, side="long")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1


def test_one_signal_per_session_max():
    """Two separate below->above reclaims in one session keep only the first."""
    def price(i: int):
        if i == 0:
            return (10.00, 10.00, 10.00, 10.00, 1000, 10_000.0)
        # First departure: bars 1..16 below.
        if 1 <= i <= 16:
            return (9.90, 9.92, 9.88, 9.90, 1000, 9_900.0)
        # First reclaim at i == 17.
        if i == 17:
            return (10.50, 10.55, 10.45, 10.50, 1000, 10_500.0)
        # Dip below again for a sustained stretch (bars 18..40).
        if 18 <= i <= 40:
            return (9.80, 9.82, 9.78, 9.80, 1000, 9_800.0)
        # Second reclaim at i == 41.
        if i == 41:
            return (10.60, 10.65, 10.55, 10.60, 1000, 10_600.0)
        return (10.60, 10.62, 10.58, 10.60, 1000, 10_600.0)
    bars = synthetic_session(date(2024, 1, 8), code="DBL", price_fn=price)
    strat = VWAPReclaimStrategy()
    params = VWAPReclaimParams(min_below_minutes=15, side="long")
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    # The kept signal is the FIRST reclaim (close 10.50), not the second.
    assert sigs.row(0, named=True)["entry_price"] == pytest.approx(10.50, rel=1e-9)


def test_typical_price_param_runs():
    """vwap_price='typical' uses (h+l+c)/3 and should still fire on a clear
    long reclaim."""
    bars = _long_reclaim_session(date(2024, 1, 8), below_for=15, reclaim_at=16)
    strat = VWAPReclaimStrategy()
    params = VWAPReclaimParams(
        vwap_price="typical", min_below_minutes=15, side="long"
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1


def test_registered_in_registry():
    from bursahack.intraday.registry import get_strategy
    spec = get_strategy("vwap_reclaim")
    assert spec.name == "vwap_reclaim"
    assert spec.version == "1.0.0"
    assert spec.params_model is VWAPReclaimParams


def test_returns_empty_on_empty_bars():
    bars = pl.DataFrame(schema={
        "ts": pl.Datetime("ns"), "code": pl.Utf8,
        "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Int64, "value": pl.Float64,
    })
    strat = VWAPReclaimStrategy()
    sigs = strat.generate_signals(
        bars, universe_members=None, params=VWAPReclaimParams()
    )
    assert sigs.height == 0
