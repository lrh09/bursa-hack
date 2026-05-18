"""Engine refuses bars/signals inside the configured holdout window."""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from bursahack.costs import MPlusRetailFee
from bursahack.intraday.engine import HoldoutBreach, IntradayEngine
from bursahack.intraday.registry import SIGNAL_SCHEMA

from tests.intraday._fixtures import synthetic_session


def test_holdout_breach_raises():
    """A signal inside [holdout_start, holdout_end] must raise HoldoutBreach."""
    bars = synthetic_session(date(2024, 6, 1), code="HOLD", base_price=10.0)
    ts = bars.get_column("ts")[10]
    sig = pl.DataFrame({
        "ts": [ts], "code": ["HOLD"], "side": [1],
        "entry_price": [10.0], "stop_price": [None],
        "target_price": [None], "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    engine = IntradayEngine(
        cost_regimes=[MPlusRetailFee()],
        holdout_start=datetime(2024, 5, 1),
        holdout_end=datetime(2024, 12, 31),
    )
    with pytest.raises(HoldoutBreach):
        engine.run(
            bars=bars, signals=sig,
            universe_members=pl.DataFrame({"date": [], "code": []}),
        )


def test_holdout_guard_passes_when_outside():
    """Bars + signals strictly outside the holdout produce holdout_excluded=True."""
    bars = synthetic_session(date(2024, 1, 3), code="HOLD", base_price=10.0)
    ts = bars.get_column("ts")[10]
    sig = pl.DataFrame({
        "ts": [ts], "code": ["HOLD"], "side": [1],
        "entry_price": [10.0], "stop_price": [None],
        "target_price": [None], "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    engine = IntradayEngine(
        cost_regimes=[MPlusRetailFee()],
        holdout_start=datetime(2024, 6, 1),
        holdout_end=datetime(2024, 12, 31),
    )
    res = engine.run(
        bars=bars, signals=sig,
        universe_members=pl.DataFrame({"date": [], "code": []}),
    )
    assert res.metadata.holdout_excluded is True
