"""LMSW volume-spike strategy: signal mechanics + no-lookahead."""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from bursahack.intraday.signals.lmsw import LMSWParams, LMSWStrategy
from tests.intraday._fixtures import synthetic_session


def _spike_session(d: date, spike_at: int, code: str = "SPK"):
    """One session where bars have noisy baseline volume (~1000 ± noise)
    and one bar spikes to 50_000. The spike bar is also an up-bar
    (close > open by ~1%). Noise is deterministic via i*7 mod 50.
    """
    def price(i: int):
        if i == spike_at:
            return (10.00, 10.15, 10.00, 10.12, 50_000, 506_000)
        v = 1000 + (i * 7) % 50  # noisy baseline so rolling std > 0
        return (10.00, 10.01, 9.99, 10.00, v, v * 10.0)
    return synthetic_session(d, code=code, price_fn=price)


def _flat_session(d: date, code: str = "FLT"):
    """All bars noisy baseline volume + tiny return -> nothing should fire."""
    def price(i: int):
        v = 1000 + (i * 7) % 50
        return (10.00, 10.005, 9.995, 10.001, v, v * 10.001)
    return synthetic_session(d, code=code, price_fn=price)


def test_no_signal_on_flat_volume_session():
    bars = _flat_session(date(2024, 1, 8))
    strat = LMSWStrategy()
    params = LMSWParams(
        z_window_bars=20, volume_z_threshold=3.0, return_floor_pct=0.10
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 0


def test_continuation_long_on_up_spike():
    bars = _spike_session(date(2024, 1, 8), spike_at=80)
    strat = LMSWStrategy()
    params = LMSWParams(
        z_window_bars=20, volume_z_threshold=3.0, return_floor_pct=0.10,
        side_mode="continuation",
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == 1  # long: bar moved up + we follow
    # Stop = entry * (1 - 0.01) = 10.12 * 0.99 = 10.0188
    assert row["stop_price"] == pytest.approx(10.12 * 0.99, rel=1e-6)


def test_reversal_short_on_up_spike():
    bars = _spike_session(date(2024, 1, 8), spike_at=80)
    strat = LMSWStrategy()
    params = LMSWParams(
        z_window_bars=20, volume_z_threshold=3.0, return_floor_pct=0.10,
        side_mode="reversal",
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1
    row = sigs.row(0, named=True)
    assert row["side"] == -1  # short: opposite of bar direction
    # Stop = entry * (1 + 0.01) for shorts
    assert row["stop_price"] == pytest.approx(10.12 * 1.01, rel=1e-6)


def test_z_window_excludes_current_bar():
    """The z-score at bar t must not include bar t's volume in the mean/std.
    Test: place a spike very early (before z_window completes). With a 20-bar
    window and spike at i=5, the z-score is undefined (NaN) because rolling
    needs >=20 prior bars. The spike must NOT fire.
    """
    bars = _spike_session(date(2024, 1, 8), spike_at=5)
    strat = LMSWStrategy()
    params = LMSWParams(
        z_window_bars=20, volume_z_threshold=3.0, return_floor_pct=0.10,
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 0, "spike before warmup should NOT fire (z-score undefined)"


def test_return_floor_gates_micro_moves():
    """Volume spikes with tiny return should be filtered out by return_floor."""
    def price(i: int):
        if i == 80:
            # Volume spike but return is only 0.05% (below 0.10% floor)
            return (10.000, 10.006, 9.998, 10.005, 50_000, 500_250)
        return (10.000, 10.001, 9.999, 10.000, 1000, 10_000)
    bars = synthetic_session(date(2024, 1, 8), code="MICRO", price_fn=price)
    strat = LMSWStrategy()
    params = LMSWParams(
        z_window_bars=20, volume_z_threshold=3.0, return_floor_pct=0.10,
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 0, "return below floor should not fire"


def test_one_signal_per_session_max():
    """If two bars in the same session would both fire, keep the first."""
    def price(i: int):
        if i in (80, 100):  # two up-spikes
            return (10.00, 10.15, 10.00, 10.12, 50_000, 506_000)
        return (10.00, 10.01, 9.99, 10.00, 1000, 10_000)
    bars = synthetic_session(date(2024, 1, 8), code="DBL", price_fn=price)
    strat = LMSWStrategy()
    params = LMSWParams(
        z_window_bars=20, volume_z_threshold=3.0, return_floor_pct=0.10,
    )
    sigs = strat.generate_signals(bars, universe_members=None, params=params)
    assert sigs.height == 1


def test_registered_in_registry():
    from bursahack.intraday.registry import get_strategy
    spec = get_strategy("lmsw")
    assert spec.name == "lmsw"
    assert spec.version == "1.0.0"
    assert spec.params_model is LMSWParams


def test_returns_empty_on_empty_bars():
    bars = pl.DataFrame(schema={
        "ts": pl.Datetime("ns"), "code": pl.Utf8,
        "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Int64, "value": pl.Float64,
    })
    strat = LMSWStrategy()
    sigs = strat.generate_signals(bars, universe_members=None, params=LMSWParams())
    assert sigs.height == 0
