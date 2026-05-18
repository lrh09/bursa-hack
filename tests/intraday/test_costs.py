"""FeeSchedule regression: MPlusRetailFee == existing daily costs.py output."""
from __future__ import annotations

import pytest

from bursahack.costs import (
    CustomFee,
    InstitutionalFee,
    MPlusRetailFee,
    cost_bps as daily_cost_bps,
    fees,
)


def test_mplus_retail_matches_daily_costs_at_two_notionals() -> None:
    """Round-trip bps reported by MPlusRetailFee == 2 * one_leg_fee / notional * 1e4.

    The daily `cost_bps()` includes sqrt-impact slippage, so we compare the
    fees-only portion: MPlusRetailFee.roundtrip_cost vs the pure-fees branch
    of the daily layer (slip_cfg disabled via huge adv).
    """
    f = MPlusRetailFee()
    for notional in [10_000.0, 250_000.0]:
        expected_one_leg = fees(notional)
        expected_rt_bps = 2.0 * expected_one_leg / notional * 10_000.0
        assert f.roundtrip_cost(notional) == pytest.approx(expected_rt_bps, rel=1e-9)


def test_institutional_flat_5bps() -> None:
    f = InstitutionalFee()
    assert f.roundtrip_cost(100_000) == pytest.approx(5.0)
    assert f.roundtrip_cost(1_000_000) == pytest.approx(5.0)
    assert f.roundtrip_cost(0) == 0.0


def test_custom_fee_explicit_fields() -> None:
    f = CustomFee(
        brokerage_rate=0.0008, brokerage_min=10.0, sst_rate=0.06,
        clearing_rate=0.0005, clearing_cap=2000.0,
        stamp_rate=0.0010, stamp_cap=1000.0,
    )
    bps_low = f.roundtrip_cost(10_000)
    bps_high = f.roundtrip_cost(1_000_000)
    # Min brokerage dominates at low notional -> higher bps
    assert bps_low > bps_high
