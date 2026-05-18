"""When session_only=True, an open position is force-flat at the last bar."""
from __future__ import annotations

from datetime import date

import polars as pl

from bursahack.costs import MPlusRetailFee
from bursahack.intraday.engine import IntradayEngine
from bursahack.intraday.registry import SIGNAL_SCHEMA

from tests.intraday._fixtures import synthetic_session


def test_session_close_flatten():
    """Long entered early; stop is way below; expect exit at last-bar close."""
    bars = synthetic_session(date(2024, 1, 3), code="X", base_price=10.0)
    # Signal at minute 5; stop set to 9.0 (never hit on flat 10.0 prices).
    ts = bars.get_column("ts")[5]
    sig = pl.DataFrame({
        "ts": [ts],
        "code": ["X"],
        "side": [1],
        "entry_price": [10.0],
        "stop_price": [9.0],
        "target_price": [None],
        "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    engine = IntradayEngine(cost_regimes=[MPlusRetailFee()], session_only=True)
    res = engine.run(
        bars=bars,
        signals=sig,
        universe_members=pl.DataFrame({"date": [], "code": []}),
    )
    trades = res.trades.filter(pl.col("regime") == "mplus_retail")
    assert trades.height == 1
    exit_ts = trades.get_column("exit_ts")[0]
    last_ts = bars.get_column("ts")[-1]
    assert exit_ts == last_ts, f"expected exit at {last_ts}, got {exit_ts}"
