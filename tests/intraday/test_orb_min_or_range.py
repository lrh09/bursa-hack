"""min_or_range_pct gates out narrow-OR sessions."""
from __future__ import annotations

from datetime import date

import polars as pl

from bursahack.intraday.signals.orb import ORBParams, ORBStrategy

from tests.intraday._fixtures import narrow_or_session


def test_min_or_range_filters_narrow_sessions():
    bars = narrow_or_session(date(2024, 1, 3), code="FLAT")
    strat = ORBStrategy()
    # OR range = 0.001 / 10.000 * 100 = 0.01%, threshold 2.0% must filter.
    params = ORBParams(min_or_range_pct=2.0, side="long")
    sigs = strat.generate_signals(
        bars=bars, universe_members=pl.DataFrame(), params=params,
    )
    assert sigs.height == 0


def test_no_threshold_emits_signals_on_narrow_session():
    """With threshold 0, the same narrow session DOES produce a signal --
    confirming the filter is the cause, not some unrelated guard."""
    bars = narrow_or_session(date(2024, 1, 3), code="FLAT")
    strat = ORBStrategy()
    params = ORBParams(min_or_range_pct=0.0, side="long")
    sigs = strat.generate_signals(
        bars=bars, universe_members=pl.DataFrame(), params=params,
    )
    assert sigs.height == 1
