"""Lock in the FULL-REVAL portfolio-margin maintenance (``pm_reval_maintenance``).

The intrinsic estimate (``pm_stress_maintenance``) ignores extrinsic (time)
value and vol, so for a LONG-PREMIUM book it is a *lower bound* on the true
maintenance — under a down-and-vol-crush stress a long-premium structure loses
MARK value (time value + vega) that the intrinsic terminal-payoff view cannot
see. Full reval reprices every leg through the L3 kernel over a spot x vol grid
and takes the worst-case MARK decline, so it is the right number for absolutes.

Properties locked here (MS_A-ish: S=389.80, sigma=0.46, T~1, r=0.045, q=0):

  1. reval maintenance >= intrinsic maintenance for the long-premium TSLA legs
     (extrinsic + vega add to the worst-case decline; intrinsic can only see
     intrinsic moves).
  2. netting still reduces combined vs the naive per-position sum (a hedged
     long+short pair nets its MARK declines when stressed as ONE leg-set).
  3. basic schema / sign sanity (same keys as the intrinsic fn, positive
     maintenance for a long-premium book, defined-risk flag carried through).

These are NEW goldens — they do not touch the intrinsic fn or its tests.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from bursahack.options import risk
from bursahack.options import margin
from bursahack.options.types import Book, Leg, MarketState, Position, Right

# Market snapshot: spot/sigma from the real TSLA book (MS_A), asof so the real
# expiries below carry ~1y of time value (the thing reval sees and intrinsic misses).
ASOF = datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc)
SPOT = 389.80
SIGMA = 0.46
MKT = MarketState(spot=SPOT, r=0.045, q=0.0, sigma=SIGMA, asof=ASOF)

EXP_2027 = date(2027, 6, 18)   # ~1y out -> meaningful extrinsic
EXP_2026 = date(2026, 9, 18)


def _call(strike, qty, expiry=EXP_2027, entry=0.0):
    return Leg(right=Right.CALL, strike=strike, expiry=expiry, qty=qty,
               mult=100, entry_price=entry, underlying="TSLA")


def _put(strike, qty, expiry=EXP_2027, entry=0.0):
    return Leg(right=Right.PUT, strike=strike, expiry=expiry, qty=qty,
               mult=100, entry_price=entry, underlying="TSLA")


# A purely long-premium leg-set (long calls): positive mark, all time value to lose.
LONG_PREMIUM_LEGS = [_call(380, 10), _call(220, 10, expiry=EXP_2026)]


# ===========================================================================
# 1. reval >= intrinsic for the long-premium book
# ===========================================================================

def test_reval_at_least_intrinsic_for_long_premium():
    reval = risk.pm_reval_maintenance(LONG_PREMIUM_LEGS, MKT)
    intrinsic = risk.pm_stress_maintenance(LONG_PREMIUM_LEGS, SPOT)
    # Both are worst-case declines (positive losses). Full reval also bleeds
    # extrinsic + vega on the down/vol-crush node, so it can only be >= intrinsic.
    assert reval["maintenance"] >= intrinsic["maintenance"] - 1e-6
    # And for this clearly long-vega book it is STRICTLY larger (time+vol matter).
    assert reval["maintenance"] > intrinsic["maintenance"]


def test_reval_single_long_call_strictly_exceeds_intrinsic():
    legs = [_call(380, 10)]  # ATM-ish long call, ~1y: lots of extrinsic + vega
    reval = risk.pm_reval_maintenance(legs, MKT)
    intrinsic = risk.pm_stress_maintenance(legs, SPOT)
    assert reval["maintenance"] > intrinsic["maintenance"]
    # binding node is on the DOWN-and-vol-CRUSH corner for a long call.
    assert reval["binding_spot"] < SPOT
    assert reval["binding_vol_pt"] <= 0.0


# ===========================================================================
# 2. netting: combined leg-set <= naive per-position sum
# ===========================================================================

def test_reval_netting_combined_below_naive_sum():
    # Hedged pair: long 380C + short 460C (a bull-call structure). Stressed as
    # ONE leg-set the long/short marks offset, so the combined worst-case mark
    # decline is <= summing each leg's standalone worst-case decline.
    long_leg = [_call(380, 5)]
    short_leg = [_call(460, -5)]
    combined = risk.pm_reval_maintenance(long_leg + short_leg, MKT)["maintenance"]
    sum_pp = (risk.pm_reval_maintenance(long_leg, MKT)["maintenance"]
              + risk.pm_reval_maintenance(short_leg, MKT)["maintenance"])
    assert combined <= sum_pp + 1e-6
    # The hedge actually nets something down (not a degenerate equality).
    assert combined < sum_pp


def test_reval_pmcc_short_wing_nets_against_long():
    # PMCC-style: deep-ITM long LEAP call + further-OTM short call. The short
    # wing's mark decline partially offsets the long's under stress -> combined
    # worst-case decline below the naive sum.
    leap = [_call(100, 1)]
    wing = [_call(600, -1)]
    combined = risk.pm_reval_maintenance(leap + wing, MKT)["maintenance"]
    sum_pp = (risk.pm_reval_maintenance(leap, MKT)["maintenance"]
              + risk.pm_reval_maintenance(wing, MKT)["maintenance"])
    assert combined <= sum_pp + 1e-6


# ===========================================================================
# 3. schema / sign / aggregator sanity
# ===========================================================================

def test_reval_schema_matches_intrinsic_keys():
    reval = risk.pm_reval_maintenance(LONG_PREMIUM_LEGS, MKT)
    intrinsic = risk.pm_stress_maintenance(LONG_PREMIUM_LEGS, SPOT)
    # reval is a superset (adds binding_vol_pt + method) of the intrinsic keys.
    for k in intrinsic:
        assert k in reval, f"reval missing key {k!r} from intrinsic schema"
    assert reval["method"] == "reval"
    assert reval["maintenance"] > 0.0          # long-premium book HAS maintenance
    assert reval["defined_risk"] is True       # long calls are defined-risk
    assert reval["flag"] == ""
    assert isinstance(reval["grid"], list) and reval["grid"]
    # grid rows carry both spot and the vol shift.
    row = reval["grid"][0]
    assert "spot" in row and "vol_pt" in row and "decline" in row


def test_reval_requires_positive_spot():
    bad = object()  # no .spot -> getattr default 0.0
    with pytest.raises(ValueError):
        risk.pm_reval_maintenance(LONG_PREMIUM_LEGS, bad)


def test_pm_maintenance_reval_is_default_and_at_least_intrinsic():
    book = Book(positions=(
        Position("TSLA", tuple(LONG_PREMIUM_LEGS)),
    ))
    mkt_map = {"TSLA": MKT}
    default = margin.pm_maintenance(book, mkt_map, cash=0.0, netliq=525898.0)
    reval = margin.pm_maintenance(book, mkt_map, cash=0.0, netliq=525898.0, method="reval")
    intrinsic = margin.pm_maintenance(book, mkt_map, cash=0.0, netliq=525898.0, method="intrinsic")
    # Default routes to reval.
    assert default["method"] == "reval"
    assert math.isclose(default["maintenance"], reval["maintenance"], rel_tol=1e-9)
    # Book-level: reval maintenance >= intrinsic for the long-premium book.
    assert reval["maintenance"] >= intrinsic["maintenance"] - 1e-6
    assert reval["maintenance"] > intrinsic["maintenance"]
    # Excess-liquidity bookkeeping intact.
    assert math.isclose(
        reval["excess_liquidity"], reval["netliq"] - reval["maintenance"], rel_tol=1e-9
    )


def test_pm_maintenance_rejects_bad_method():
    book = Book(positions=(Position("TSLA", tuple(LONG_PREMIUM_LEGS)),))
    with pytest.raises(ValueError):
        margin.pm_maintenance(book, {"TSLA": MKT}, method="bogus")
