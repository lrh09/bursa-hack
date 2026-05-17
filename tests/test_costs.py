"""Lock in the MPlus fee model against hand-computed scenarios."""
from __future__ import annotations

import math

from bursahack.costs import (
    FeeConfig,
    SlippageConfig,
    cost_bps,
    fees,
    slippage,
    slippage_bps,
    total_cost,
)


def test_tiny_ticket_hits_minimum_brokerage():
    # RM 1,000 ticket: 0.05% = RM 0.50 -- but min is RM 8 -> brokerage = 8 * 1.08
    # clearing 0.03% = 0.30, stamp 0.10% = 1.00
    # total = 8.64 + 0.30 + 1.00 = 9.94
    assert math.isclose(fees(1000.0), 8 * 1.08 + 0.30 + 1.00, rel_tol=1e-9)


def test_mid_ticket_above_min():
    # RM 50,000 ticket: 0.05% = 25 -> brokerage = 25 * 1.08 = 27.00
    # clearing = 15.00, stamp = 50.00 -> total 92.00
    assert math.isclose(fees(50_000.0), 25 * 1.08 + 15.0 + 50.0, rel_tol=1e-9)


def test_large_ticket_hits_caps():
    # RM 5,000,000 ticket: 0.05% = 2500 -> brokerage = 2500 * 1.08 = 2700
    # clearing = 0.03% = 1500 but capped at 1000
    # stamp = 0.10% = 5000 but capped at 1000
    # total = 2700 + 1000 + 1000 = 4700
    assert math.isclose(fees(5_000_000.0), 2500 * 1.08 + 1000.0 + 1000.0, rel_tol=1e-9)


def test_zero_or_negative_notional_returns_zero():
    assert fees(0.0) == 0.0
    assert fees(-100.0) == 0.0


def test_typical_retail_ticket_cost_bps():
    # RM 12,000 ticket (a 30-stock RM 350k portfolio).
    # brokerage = max(6, 8) * 1.08 = 8.64; clearing = 3.60; stamp = 12.00 -> 24.24
    # one-leg = 24.24 -> 20.2 bps; round-trip = 40.4 bps before slippage
    f = fees(12_000.0)
    assert math.isclose(f, 8 * 1.08 + 3.60 + 12.0, rel_tol=1e-9)
    bps_one_leg = f / 12_000.0 * 10_000.0
    assert 19.0 < bps_one_leg < 22.0


def test_slippage_at_zero_participation_floors():
    # Tiny order vs huge ADV -> very small participation -> floors at min_bps
    assert slippage_bps(notional=1_000.0, adv_20d=1_000_000_000.0) == 2.0


def test_slippage_at_full_participation_is_k():
    # Order = ADV -> participation = 1 -> sqrt(1) = 1 -> k_bps = 10 bps
    assert math.isclose(slippage_bps(notional=1_000_000.0, adv_20d=1_000_000.0), 10.0, rel_tol=1e-9)


def test_slippage_caps_at_max_bps_for_illiquid():
    # 100x ADV would give sqrt(100) * 10 = 100 bps; we cap at 200 anyway
    # 1000x ADV would give sqrt(1000) * 10 ~= 316 bps -> capped at 200
    assert slippage_bps(notional=1_000_000.0, adv_20d=1_000.0) == 200.0


def test_slippage_zero_adv_returns_max_bps():
    assert slippage_bps(notional=1_000.0, adv_20d=0.0) == 200.0


def test_total_cost_decomposition_sums():
    f, s, t = total_cost(notional=50_000.0, adv_20d=500_000.0)
    assert math.isclose(t, f + s, rel_tol=1e-12)


def test_round_trip_cost_bps_sanity():
    # Liquid mid-cap: 12k order vs 500k ADV -> participation 2.4% -> slip sqrt(0.024)*10 ~ 1.55 bps -> floored to 2 bps
    # fees ~20.2 bps, slip 2 bps -> 22.2 one-leg, 44.4 round-trip
    rt = cost_bps(notional=12_000.0, adv_20d=500_000.0)
    assert 40.0 < rt < 50.0


def test_custom_fee_config_overrides():
    # If we ever want to simulate a different broker, the config plugs in cleanly.
    cheaper = FeeConfig(brokerage_rate=0.0001, brokerage_min=3.0, sst_rate=0.06,
                        clearing_rate=0.0, clearing_cap=0.0,
                        stamp_rate=0.0, stamp_cap=0.0)
    assert math.isclose(fees(100_000.0, cheaper), 0.0001 * 100_000 * 1.06, rel_tol=1e-9)
