"""Golden-file test for the ORB strategy + engine, on a handcrafted session."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from bursahack.costs import MPlusRetailFee
from bursahack.intraday.engine import IntradayEngine
from bursahack.intraday.signals.orb import ORBParams, ORBStrategy

from tests.intraday._fixtures import orb_golden_session

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _write_fixture_if_absent():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    p = FIXTURE_DIR / "orb_golden_session.parquet"
    if not p.exists():
        orb_golden_session(date(2024, 1, 3), code="GOLD").write_parquet(p)


def test_orb_golden_long_breakout():
    """5-min OR (high=10.10, low=10.00); breakout bar i=5 with high=10.20.
    Strategy params: long-only, 1% stop, exit at close.
    Expected: exactly 1 long entry, entry_px=10.11 (next-bar open i=6),
    no stop hit, exit at session-close = 10.30.
    """
    _write_fixture_if_absent()
    bars = pl.read_parquet(FIXTURE_DIR / "orb_golden_session.parquet")

    strat = ORBStrategy()
    params = ORBParams(
        opening_range_minutes=5, stop_pct=1.0,
        exit_policy="session_close", side="long",
        min_or_range_pct=0.0,
    )
    sigs = strat.generate_signals(
        bars=bars, universe_members=pl.DataFrame(), params=params,
    )
    assert sigs.height == 1, f"expected 1 ORB signal, got {sigs.height}"
    # The breakout bar in the fixture is index 5 (i.e. 6th bar = 09:05 KL).
    sig_ts = sigs.get_column("ts")[0]
    breakout_ts = bars.get_column("ts")[5]
    assert sig_ts == breakout_ts

    engine = IntradayEngine(cost_regimes=[MPlusRetailFee()])
    res = engine.run(
        bars=bars, signals=sigs,
        universe_members=pl.DataFrame({"date": [], "code": []}),
    )
    trades = res.trades.filter(pl.col("regime") == "mplus_retail")
    assert trades.height == 1
    entry_px = trades.get_column("entry_px")[0]
    exit_px = trades.get_column("exit_px")[0]
    assert entry_px == pytest.approx(10.11, abs=1e-6), (
        f"entry should be 10.11 (next-bar open of breakout); got {entry_px}"
    )
    # Exit at the very last bar close (session_close flatten); last bar close = 10.30.
    last_close = bars.get_column("close")[-1]
    assert exit_px == pytest.approx(last_close, abs=1e-6)
    # Sanity: gross_ret positive and ~ (last_close - 10.11) / 10.11.
    gross = trades.get_column("gross_ret")[0]
    assert gross == pytest.approx((last_close - 10.11) / 10.11, abs=1e-6)
