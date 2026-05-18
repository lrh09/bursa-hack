"""Engine MUST fill at next-bar-open. NEVER same-bar.

This is the load-bearing guarantee everything else depends on.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from bursahack.costs import MPlusRetailFee
from bursahack.intraday.engine import IntradayEngine
from bursahack.intraday.registry import SIGNAL_SCHEMA

from tests.intraday._fixtures import synthetic_session


def _ramping_session():
    """1-day session with deterministic distinct OHLC per bar so we can
    pin down the exact bar a fill landed on."""
    def price(i: int):
        # Each bar i: open=10+0.01*i, close=10+0.01*(i+0.5), high/low simple.
        o = 10.0 + 0.01 * i
        c = 10.0 + 0.01 * (i + 0.5)
        h = max(o, c) + 0.001
        l = min(o, c) - 0.001
        return (o, h, l, c, 1000, 1000 * c)
    return synthetic_session(date(2024, 1, 3), code="RAMP", price_fn=price)


@pytest.mark.parametrize("signal_idx", [10, 50, 100, 150, 200, 250, 300, 358])
def test_signal_fill_is_next_bar_open(signal_idx: int):
    """A signal at bar i must fill at bar (i+1).open, never bar i.{open,close}.

    Parameterised across 8 positions spanning the session.
    """
    bars = _ramping_session()
    ts_arr = bars.get_column("ts").to_list()
    open_arr = bars.get_column("open").to_list()
    close_arr = bars.get_column("close").to_list()

    sig_ts = ts_arr[signal_idx]
    sig_df = pl.DataFrame({
        "ts": [sig_ts],
        "code": ["RAMP"],
        "side": [1],
        "entry_price": [close_arr[signal_idx]],
        "stop_price": [None],
        "target_price": [None],
        "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    engine = IntradayEngine(cost_regimes=[MPlusRetailFee()])
    res = engine.run(
        bars=bars,
        signals=sig_df,
        universe_members=pl.DataFrame({"date": [], "code": []}),
    )
    trades = res.trades.filter(pl.col("regime") == "mplus_retail")
    assert trades.height == 1, f"expected 1 trade, got {trades.height}"
    entry_px = trades.get_column("entry_px")[0]

    next_open = open_arr[signal_idx + 1]
    same_bar_close = close_arr[signal_idx]
    same_bar_open = open_arr[signal_idx]

    assert entry_px == pytest.approx(next_open), (
        f"fill px {entry_px} != next-bar-open {next_open}; "
        f"same-bar open={same_bar_open}, same-bar close={same_bar_close}"
    )
    assert entry_px != pytest.approx(same_bar_open)
    assert entry_px != pytest.approx(same_bar_close)
