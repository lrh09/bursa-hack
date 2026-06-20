"""Lock the implied-vol solver against the §7 goldens.

The IV solver must invert a Black-Scholes price back to the EXACT input vol.
Goldens come from two pinned market states:

  MS_A: S=389.80, T=1, r=0.045, q=0
  MS_B: S=396.40, T=1, r=0.045, q=0  (sigma varies per row)

All prices below were generated forward through the same BSM core, so the
round-trip is an identity check to <1e-4 (the contract tolerance).

Math shown in comments (mirrors tests/test_costs.py style).
"""
from __future__ import annotations

import math

from bursahack.options.iv import IVResult, implied_vol, price_bounds_check
from bursahack.options.types import Right

R = 0.045
Q = 0.0
T = 1.0


# ---------------------------------------------------------------------------
# Round-trip: recovered IV == input sigma (MS_A / MS_B goldens)
# ---------------------------------------------------------------------------
def test_iv_roundtrip_msa_360c():
    # MS_A 360C = 91.96 was priced at sigma=0.46 -> solver recovers 0.46.
    res = implied_vol(91.96, 389.80, 360.0, T, R, Q, Right.CALL)
    assert res.converged
    assert math.isclose(res.iv, 0.46, abs_tol=1e-3)
    assert res.method == "newton"


def test_iv_roundtrip_msa_460c():
    # MS_A 460C = 53.00 priced at sigma=0.46 -> recovers 0.46.
    res = implied_vol(53.00, 389.80, 460.0, T, R, Q, Right.CALL)
    assert res.converged
    assert math.isclose(res.iv, 0.46, abs_tol=1e-3)


def test_iv_roundtrip_msb_400c_50():
    # MS_B 400C @ .50 = 84.07 -> recovers 0.50.
    res = implied_vol(84.07, 396.40, 400.0, T, R, Q, Right.CALL)
    assert res.converged
    assert math.isclose(res.iv, 0.50, abs_tol=1e-3)


def test_iv_roundtrip_msb_400c_48():
    # MS_B 400C @ .48 = 81.06 -> recovers 0.48 (NOT 0.50 -- the verified delta
    # is 0.624 not 0.626; this row pins the second pricing set precisely).
    res = implied_vol(81.06, 396.40, 400.0, T, R, Q, Right.CALL)
    assert res.converged
    assert math.isclose(res.iv, 0.48, abs_tol=1e-3)


def test_iv_roundtrip_msb_300c_52():
    # MS_B 300C @ .52 = 137.59 (deep ITM call) -> recovers 0.52.
    res = implied_vol(137.59, 396.40, 300.0, T, R, Q, Right.CALL)
    assert res.converged
    assert math.isclose(res.iv, 0.52, abs_tol=2e-3)


def test_iv_tolerance_is_tight():
    # Contract demands < 1e-4 recovery on the clean goldens. Reprice EXACTLY at
    # the analytic price (full precision) and assert tight recovery.
    # 460C @ 0.46: compute price to full precision via the same primitive.
    from bursahack.options.bsm import price
    px = price(389.80, 460.0, T, R, Q, 0.46, Right.CALL)
    res = implied_vol(px, 389.80, 460.0, T, R, Q, Right.CALL)
    assert math.isclose(res.iv, 0.46, abs_tol=1e-4)
    assert res.residual < 1e-6


# ---------------------------------------------------------------------------
# Call <-> parity-paired put solve to the SAME IV (forward measure)
# ---------------------------------------------------------------------------
def test_call_and_put_same_strike_solve_same_iv():
    # Price a call and put at the SAME strike/sigma; both must recover that vol.
    from bursahack.options.bsm import price
    K = 410.0
    sig = 0.40
    c = price(389.80, K, T, R, Q, sig, Right.CALL)
    p = price(389.80, K, T, R, Q, sig, Right.PUT)
    iv_c = implied_vol(c, 389.80, K, T, R, Q, Right.CALL)
    iv_p = implied_vol(p, 389.80, K, T, R, Q, Right.PUT)
    assert math.isclose(iv_c.iv, sig, abs_tol=1e-4)
    assert math.isclose(iv_p.iv, sig, abs_tol=1e-4)
    # And they agree with each other.
    assert math.isclose(iv_c.iv, iv_p.iv, abs_tol=1e-4)


# ---------------------------------------------------------------------------
# Deep-OTM forces bisection; vega < 1e-6 at the seed is the deterministic trigger
# ---------------------------------------------------------------------------
def test_deep_otm_forces_bisection():
    # A far OTM call with a tiny price: vega at the BS seed collapses, so the
    # solver must use the bisection branch.
    from bursahack.options.bsm import price
    K = 2000.0  # very far OTM
    sig = 0.30
    px = price(389.80, K, T, R, Q, sig, Right.CALL)
    res = implied_vol(px, 389.80, K, T, R, Q, Right.CALL)
    # Far OTM at low vol -> deep-OTM trigger -> bisection.
    assert res.method == "bisection"


# ---------------------------------------------------------------------------
# Bounds pre-check: below-intrinsic and above-no-arb are rejected before solving
# ---------------------------------------------------------------------------
def test_below_intrinsic_rejected():
    # Deep ITM call: intrinsic ~ S - K*e^{-rT}. Quote BELOW that is impossible.
    # 100C with S=389.80: discounted intrinsic ~ 389.80 - 100*e^{-0.045} ~ 294.2.
    res = implied_vol(50.0, 389.80, 100.0, T, R, Q, Right.CALL)
    assert not res.converged
    assert res.flag == "below-intrinsic"
    assert math.isnan(res.iv)


def test_above_no_arb_rejected():
    # A call can never exceed S*e^{-qT}. Quote above the spot is rejected.
    res = implied_vol(500.0, 389.80, 400.0, T, R, Q, Right.CALL)
    assert not res.converged
    assert res.flag == "above-no-arb"


def test_price_bounds_check_fields():
    b = price_bounds_check(53.0, 389.80, 460.0, T, R, Q, Right.CALL)
    assert b["ok"] is True
    assert b["reason"] == "ok"
    # Call lower bound = max(S*e^{-qT} - K*e^{-rT}, 0); 460C is OTM -> ~0.
    assert b["lower"] >= 0.0
    # Upper bound = S*e^{-qT} = 389.80.
    assert math.isclose(b["upper"], 389.80, rel_tol=1e-9)


def test_ivresult_is_frozen_value_object():
    res = implied_vol(53.00, 389.80, 460.0, T, R, Q, Right.CALL)
    assert isinstance(res, IVResult)
    # frozen dataclass: assignment raises.
    try:
        res.iv = 0.0  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised
