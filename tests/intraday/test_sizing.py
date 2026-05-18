"""Position-sizing tests: formula, participation cap, min-notional gate,
and end-to-end no-lookahead with sized engine.
"""
from __future__ import annotations

import math
from datetime import date

import polars as pl
import pytest

from bursahack.costs import InstitutionalFee, MPlusRetailFee
from bursahack.intraday.engine import IntradayEngine
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.registry import SIGNAL_SCHEMA
from bursahack.intraday.sizing import (
    FixedFractionNotionalSizer,
    FixedFractionalRiskSizer,
)

from tests.intraday._fixtures import synthetic_session


# ---------------------------------------------------------------------------
# FixedFractionalRiskSizer formula spot-checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "equity, entry, stop, adv, cap, pct, expected",
    [
        # risk_rm = 100_000 * 0.5/100 = 500
        # risk_per_share = |10 - 9.9| = 0.1
        # target = floor(500 / 0.1) = 5000
        # cap = floor(0.10 * 100_000) = 10_000 -> uncapped wins
        (100_000.0, 10.0, 9.9, 100_000.0, 0.10, 0.5, 5000),
        # Wider stop -> fewer shares.
        # risk_per_share = 0.5; target = 1000; cap = 10_000 -> 1000
        (100_000.0, 10.0, 9.5, 100_000.0, 0.10, 0.5, 1000),
        # Participation cap binds: adv=5000, cap=10% -> cap_qty=500
        # target = 5000 -> cap wins
        (100_000.0, 10.0, 9.9, 5000.0, 0.10, 0.5, 500),
        # Larger equity scales up linearly. equity=1M -> risk=5000 -> target=50000
        # cap = 0.10 * 100_000 = 10_000 -> cap wins
        (1_000_000.0, 10.0, 9.9, 100_000.0, 0.10, 0.5, 10_000),
        # Different risk pct. 1% of 100k = 1000; target = floor(1000/0.1) = 10_000
        # cap = 10_000 -> tie / both yield 10_000
        (100_000.0, 10.0, 9.9, 100_000.0, 0.10, 1.0, 10_000),
    ],
)
def test_fixed_fractional_risk_formula(
    equity, entry, stop, adv, cap, pct, expected
):
    sizer = FixedFractionalRiskSizer(risk_per_trade_pct=pct)
    qty = sizer.size(
        equity=equity,
        entry_price=entry,
        stop_price=stop,
        adv_bar_shares=adv,
        participation_cap=cap,
    )
    assert qty == expected, f"expected {expected} got {qty}"


def test_fixed_fractional_risk_refuses_null_stop():
    """A risk-based sizer MUST refuse to size without a stop."""
    sizer = FixedFractionalRiskSizer()
    with pytest.raises(ValueError, match="stop_price is required"):
        sizer.size(
            equity=100_000.0, entry_price=10.0, stop_price=None,
            adv_bar_shares=10_000.0, participation_cap=0.10,
        )


def test_fixed_fractional_risk_zero_risk_returns_zero():
    """entry == stop -> risk_per_share = 0 -> qty = 0 (no divide-by-zero crash)."""
    sizer = FixedFractionalRiskSizer()
    qty = sizer.size(
        equity=100_000.0, entry_price=10.0, stop_price=10.0,
        adv_bar_shares=10_000.0, participation_cap=0.10,
    )
    assert qty == 0


def test_participation_cap_binds():
    """If unconstrained target > cap_qty, cap wins."""
    sizer = FixedFractionalRiskSizer(risk_per_trade_pct=10.0)  # huge risk -> target>>cap
    qty = sizer.size(
        equity=1_000_000.0, entry_price=10.0, stop_price=9.99,
        adv_bar_shares=1000.0, participation_cap=0.10,
    )
    assert qty == 100  # floor(0.10 * 1000) = 100


# ---------------------------------------------------------------------------
# FixedFractionNotionalSizer
# ---------------------------------------------------------------------------


def test_notional_sizer_basic():
    sizer = FixedFractionNotionalSizer(fraction=0.02)
    # 2% of 100k = 2000 RM ; at 10 RM/share = 200 shares
    qty = sizer.size(
        equity=100_000.0, entry_price=10.0, stop_price=None,
        adv_bar_shares=100_000.0, participation_cap=0.10,
    )
    assert qty == 200


def test_notional_sizer_participation_cap():
    sizer = FixedFractionNotionalSizer(fraction=0.5)
    # 50% of 100k = 50_000 RM ; at 10 RM/share = 5000 shares
    # adv=1000, cap=10% -> cap_qty = 100 -> cap wins
    qty = sizer.size(
        equity=100_000.0, entry_price=10.0, stop_price=None,
        adv_bar_shares=1000.0, participation_cap=0.10,
    )
    assert qty == 100


# ---------------------------------------------------------------------------
# Engine-level: min-notional gate is per-regime
# ---------------------------------------------------------------------------


def _ramping_bars():
    def price(i):
        # Larger absolute price so RM 16k min-notional is reachable with realistic qty.
        o = 50.0 + 0.01 * i
        c = 50.0 + 0.01 * (i + 0.5)
        h = max(o, c) + 0.005
        l = min(o, c) - 0.005
        return (o, h, l, c, 1000, 1000 * c)
    return synthetic_session(date(2024, 1, 3), code="MNQ", price_fn=price)


def test_min_notional_drops_retail_keeps_institutional():
    """A trade sized below RM 16k for retail should produce a zero-qty
    placeholder for retail but the real trade for institutional.
    """
    bars = _ramping_bars()
    ts = bars.get_column("ts")[10]
    # Stop very near entry -> tiny risk_per_share -> in theory huge qty.
    # We use a small equity so risk_rm is small -> small qty -> small notional.
    sig = pl.DataFrame({
        "ts": [ts], "code": ["MNQ"], "side": [1],
        "entry_price": [50.0], "stop_price": [49.9],
        "target_price": [None], "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    # Build minimal sigma+adv tables so the engine runs in sized mode.
    bars_ts = bars.get_column("ts").to_list()
    sigma_table = pl.DataFrame({
        "ts": bars_ts,
        "code": ["MNQ"] * len(bars_ts),
        "sigma": [0.001] * len(bars_ts),
    }, schema={"ts": pl.Datetime("ns"), "code": pl.Utf8, "sigma": pl.Float64})
    # Single ADV row keyed by the session date (matches engine join shape).
    adv_table = pl.DataFrame({
        "date": [date(2024, 1, 3)],
        "code": ["MNQ"],
        "adv_bar_shares": [1_000.0],   # tiny ADV -> tiny qty -> sub-min-notional
        "adv_bar_value_rm": [50_000.0],
    }, schema={"date": pl.Date, "code": pl.Utf8,
               "adv_bar_shares": pl.Float64, "adv_bar_value_rm": pl.Float64})

    sizer = FixedFractionalRiskSizer(risk_per_trade_pct=0.1)
    engine = IntradayEngine(
        cost_regimes=[MPlusRetailFee(), InstitutionalFee()],
        sizer=sizer,
        starting_equity=10_000.0,   # 0.1% of 10k = 10 RM risk -> tiny qty
        max_position_pct_adv=0.10,
    )
    res = engine.run(
        bars=bars, signals=sig,
        universe_members=pl.DataFrame({"date": [], "code": []}),
        sigma_table=sigma_table, adv_table=adv_table,
    )
    retail = res.trades.filter(pl.col("regime") == "mplus_retail")
    insto = res.trades.filter(pl.col("regime") == "institutional")
    assert retail.height == 1 and insto.height == 1, "expected one row per regime"

    retail_qty = retail.get_column("qty")[0]
    insto_qty = insto.get_column("qty")[0]
    assert retail_qty == 0.0, f"retail should be zero-qty (sub-min-notional); got {retail_qty}"
    assert insto_qty > 0.0, f"insto should still take the trade; got {insto_qty}"


# ---------------------------------------------------------------------------
# Sized engine remains no-lookahead
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("signal_idx", [10, 100, 250])
def test_sized_engine_still_next_bar_open(signal_idx: int):
    """Even with sizing wired in, a signal at bar i must fill at bar (i+1).open."""
    def price(i):
        o = 50.0 + 0.01 * i
        c = 50.0 + 0.01 * (i + 0.5)
        return (o, max(o, c) + 0.001, min(o, c) - 0.001, c, 1000, 1000 * c)
    bars = synthetic_session(date(2024, 1, 3), code="RAMP", price_fn=price)
    ts_arr = bars.get_column("ts").to_list()
    open_arr = bars.get_column("open").to_list()

    sig = pl.DataFrame({
        "ts": [ts_arr[signal_idx]], "code": ["RAMP"], "side": [1],
        "entry_price": [50.0], "stop_price": [49.5],
        "target_price": [None], "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    sigma_table = pl.DataFrame({
        "ts": ts_arr,
        "code": ["RAMP"] * len(ts_arr),
        "sigma": [0.001] * len(ts_arr),
    }, schema={"ts": pl.Datetime("ns"), "code": pl.Utf8, "sigma": pl.Float64})
    adv_table = pl.DataFrame({
        "date": [date(2024, 1, 3)],
        "code": ["RAMP"],
        "adv_bar_shares": [100_000.0],
        "adv_bar_value_rm": [5_000_000.0],
    }, schema={"date": pl.Date, "code": pl.Utf8,
               "adv_bar_shares": pl.Float64, "adv_bar_value_rm": pl.Float64})

    engine = IntradayEngine(
        cost_regimes=[InstitutionalFee()],
        sizer=FixedFractionalRiskSizer(risk_per_trade_pct=0.5),
    )
    res = engine.run(
        bars=bars, signals=sig,
        universe_members=pl.DataFrame({"date": [], "code": []}),
        sigma_table=sigma_table, adv_table=adv_table,
    )
    trades = res.trades.filter(pl.col("qty") > 0)
    assert trades.height == 1
    entry_px = trades.get_column("entry_px")[0]
    next_open = open_arr[signal_idx + 1]
    assert entry_px == pytest.approx(next_open), (
        f"sized engine broke no-lookahead: fill {entry_px} vs next-open {next_open}"
    )
