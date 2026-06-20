"""Lock the European BSM pricing core, the full greek surface, and put-call parity
against hand-/independently-verified goldens.

Two market states underlie the goldens (never mix them):
  MS_A: S=389.80, sigma=0.46, T=1, r=0.045, q=0   (bull-call economics, ladder)
  MS_B: S=396.40,            T=1, r=0.045, q=0     (second pricing set; sigma per row)

All deltas/prices below were independently reproduced with the same closed forms
the module ships; the math is shown in comments.
"""
from __future__ import annotations

import math
import random

import pytest

from bursahack.options.american import cross_check, fd_greek
from bursahack.options.bs import (
    Greeks,
    Right,
    all_greeks,
    d1d2,
    first_order,
    implied_forward,
    norm_cdf,
    norm_pdf,
    parity_residual,
    price,
    spot_time_greeks,
    vol_greeks,
)

R = 0.045
Q = 0.0

# ---------------------------------------------------------------------------
# Normal primitives
# ---------------------------------------------------------------------------


def test_norm_cdf_pdf_anchors():
    # N(0)=0.5, n(0)=1/sqrt(2pi)=0.39894..., N(1.96)~0.975
    assert math.isclose(norm_cdf(0.0), 0.5, abs_tol=1e-12)
    assert math.isclose(norm_pdf(0.0), 1.0 / math.sqrt(2 * math.pi), rel_tol=1e-12)
    assert math.isclose(norm_cdf(1.959963985), 0.975, abs_tol=1e-6)
    # symmetry
    assert math.isclose(norm_cdf(-1.3) + norm_cdf(1.3), 1.0, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# MS_A price + delta goldens  (S=389.80, sigma=0.46, T=1)
# ---------------------------------------------------------------------------


def test_ms_a_360_call_price_and_delta():
    # 360C @ MS_A = 91.96, delta = 0.692
    c = price(389.80, 360.0, 1.0, R, Q, 0.46, Right.CALL)
    assert math.isclose(c, 91.96, abs_tol=0.01)
    fo = first_order(389.80, 360.0, 1.0, R, Q, 0.46, Right.CALL)
    assert math.isclose(fo["delta"], 0.692, abs_tol=5e-4)


def test_ms_a_460_call_price_and_delta():
    # 460C @ MS_A = 53.00, delta = 0.487
    c = price(389.80, 460.0, 1.0, R, Q, 0.46, Right.CALL)
    assert math.isclose(c, 53.00, abs_tol=0.01)
    fo = first_order(389.80, 460.0, 1.0, R, Q, 0.46, Right.CALL)
    assert math.isclose(fo["delta"], 0.487, abs_tol=5e-4)


# ---------------------------------------------------------------------------
# MS_B second pricing set  (S=396.40, T=1)  — verified deltas pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "K, sigma, exp_price, exp_delta",
    [
        (400.0, 0.50, 84.07, 0.626),  # 400C@.50
        (400.0, 0.48, 81.06, 0.624),  # 400C@.48  (verified 0.624, NOT 0.626)
        (380.0, 0.50, 92.66, 0.664),  # 380C@.50
        (320.0, 0.52, 125.78, 0.776),  # 320C@.52
        (300.0, 0.52, 137.59, 0.811),  # 300C@.52  (verified 0.811)
    ],
)
def test_ms_b_second_pricing_set(K, sigma, exp_price, exp_delta):
    c = price(396.40, K, 1.0, R, Q, sigma, Right.CALL)
    assert math.isclose(c, exp_price, abs_tol=0.01)
    fo = first_order(396.40, K, 1.0, R, Q, sigma, Right.CALL)
    assert math.isclose(fo["delta"], exp_delta, abs_tol=5e-4)


def test_spot_mixup_trap():
    # The same 400C@.50 priced at the WRONG spot (S=389.80) is 79.98, not 84.07.
    # This guards against an agent reading the spot from an adjacent table row.
    wrong = price(389.80, 400.0, 1.0, R, Q, 0.50, Right.CALL)
    right = price(396.40, 400.0, 1.0, R, Q, 0.50, Right.CALL)
    assert math.isclose(wrong, 79.98, abs_tol=0.01)
    assert math.isclose(right, 84.07, abs_tol=0.01)
    assert not math.isclose(wrong, right, abs_tol=1.0)


# ---------------------------------------------------------------------------
# Delta bounds (L2: puts first-class, both rights well-formed)
# ---------------------------------------------------------------------------


def test_delta_bounds_call_in_zero_one():
    for K in (200.0, 360.0, 460.0, 800.0):
        d = first_order(389.80, K, 1.0, R, Q, 0.46, Right.CALL)["delta"]
        assert 0.0 <= d <= 1.0  # call delta in [0, 1] (q=0)


def test_delta_bounds_put_in_minus_one_zero():
    for K in (200.0, 360.0, 460.0, 800.0):
        d = first_order(389.80, K, 1.0, R, Q, 0.46, Right.PUT)["delta"]
        assert -1.0 <= d <= 0.0  # put delta in [-1, 0] (q=0)


def test_call_minus_put_delta_is_discount_factor():
    # delta_call - delta_put = e^(-qT) (here q=0 so = 1)
    dc = first_order(389.80, 410.0, 1.0, R, Q, 0.46, Right.CALL)["delta"]
    dp = first_order(389.80, 410.0, 1.0, R, Q, 0.46, Right.PUT)["delta"]
    assert math.isclose(dc - dp, math.exp(-Q * 1.0), rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Put-call parity  (random fuzz, exact to 1e-9)
# ---------------------------------------------------------------------------


def test_parity_exact_over_random_params():
    rng = random.Random(20260618)
    for _ in range(500):
        S = rng.uniform(1.0, 5000.0)
        K = rng.uniform(1.0, 5000.0)
        T = rng.uniform(1.0 / 365.0, 3.0)
        sigma = rng.uniform(0.01, 3.0)
        q = rng.uniform(0.0, 0.1)
        C = price(S, K, T, R, q, sigma, Right.CALL)
        P = price(S, K, T, R, q, sigma, Right.PUT)
        # (C - P) == S*e^(-qT) - K*e^(-rT)
        assert abs(parity_residual(C, P, S, K, T, R, q)) < 1e-9


def test_parity_residual_zero_for_consistent_pair():
    S, K, T, sigma = 389.80, 460.0, 1.0, 0.46
    C = price(S, K, T, R, Q, sigma, Right.CALL)
    P = price(S, K, T, R, Q, sigma, Right.PUT)
    assert abs(parity_residual(C, P, S, K, T, R, Q)) < 1e-10


def test_implied_forward():
    # F = S*e^((r-q)T)
    assert math.isclose(implied_forward(389.80, 1.0, R, Q), 389.80 * math.exp(R), rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Higher-order analytic greeks == finite differences  (5dp / cross_check)
# ---------------------------------------------------------------------------


def _bs_pricer(**pp: float) -> float:
    return price(pp["S"], pp["K"], pp["T"], pp["r"], pp["q"], pp["sigma"], pp["right"])


@pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
def test_first_order_greeks_match_fd(right):
    S, K, T, sigma = 389.80, 460.0, 1.0, 0.46
    g = all_greeks(S, K, T, R, Q, sigma, right)
    params = dict(S=S, K=K, T=T, r=R, q=Q, sigma=sigma, right=right)
    # delta / gamma direct; theta per-year via -dPrice/dT; vega raw/100; rho raw/100.
    assert cross_check(g.delta, fd_greek(_bs_pricer, params, "delta"))[0]
    assert cross_check(g.gamma, fd_greek(_bs_pricer, params, "gamma"))[0]
    assert cross_check(g.theta_yr, fd_greek(_bs_pricer, params, "theta"))[0]
    assert cross_check(g.vega, fd_greek(_bs_pricer, params, "vega") / 100.0)[0]
    assert cross_check(g.rho, fd_greek(_bs_pricer, params, "rho") / 100.0)[0]


@pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
def test_higher_order_greeks_match_fd(right):
    # vanna, vomma, charm, speed, zomma, color (+ veta, ultima) analytic == fd to 5dp.
    S, K, T, sigma = 389.80, 460.0, 1.0, 0.46
    g = all_greeks(S, K, T, R, Q, sigma, right)
    params = dict(S=S, K=K, T=T, r=R, q=Q, sigma=sigma, right=right)
    pairs = [
        (g.vanna, fd_greek(_bs_pricer, params, "vanna") / 100.0),
        (g.vomma, fd_greek(_bs_pricer, params, "vomma") / 100.0 / 100.0),
        (g.charm_day, fd_greek(_bs_pricer, params, "charm") / 365.0),
        (g.speed, fd_greek(_bs_pricer, params, "speed")),
        (g.zomma, fd_greek(_bs_pricer, params, "zomma") / 100.0),
        (g.color_day, fd_greek(_bs_pricer, params, "color") / 365.0),
        (g.veta, fd_greek(_bs_pricer, params, "veta") / 100.0),
        (g.ultima, fd_greek(_bs_pricer, params, "ultima") / 100.0**3),
    ]
    for analytic, numeric in pairs:
        ok, gap = cross_check(analytic, numeric, tol=1e-4)
        assert ok, f"greek mismatch: analytic={analytic} numeric={numeric} gap={gap}"


def test_gamma_vega_right_independent():
    # gamma and vega are the same for a call and put at the same strike.
    gc = all_greeks(389.80, 460.0, 1.0, R, Q, 0.46, Right.CALL)
    gp = all_greeks(389.80, 460.0, 1.0, R, Q, 0.46, Right.PUT)
    assert math.isclose(gc.gamma, gp.gamma, rel_tol=1e-12)
    assert math.isclose(gc.vega, gp.vega, rel_tol=1e-12)
    assert math.isclose(gc.vomma, gp.vomma, rel_tol=1e-12)


def test_all_greeks_returns_full_surface():
    g = all_greeks(389.80, 460.0, 1.0, R, Q, 0.46, Right.CALL)
    assert isinstance(g, Greeks)
    # every higher-order field is populated analytically (not None)
    for field in ("vanna", "vomma", "veta", "ultima", "charm_day",
                  "speed", "color_day", "zomma"):
        assert getattr(g, field) is not None


# ---------------------------------------------------------------------------
# Degenerate / limit guards (no NaN)
# ---------------------------------------------------------------------------


def test_zero_total_vol_degrades_to_intrinsic_not_nan():
    # sigma -> 0, ITM call: price -> discounted intrinsic at the forward, never NaN.
    c = price(450.0, 400.0, 1.0, R, Q, 1e-13, Right.CALL)
    fwd = 450.0 * math.exp(R * 1.0)
    expected = math.exp(-R * 1.0) * max(fwd - 400.0, 0.0)
    assert not math.isnan(c)
    assert math.isclose(c, expected, rel_tol=1e-9)
    # OTM call -> 0
    c2 = price(350.0, 400.0, 1.0, R, Q, 1e-13, Right.CALL)
    assert math.isclose(c2, 0.0, abs_tol=1e-9)


def test_d1d2_zero_vol_limits():
    # ITM forward -> Nd1 = Nd2 = 1; OTM -> 0
    itm = d1d2(450.0, 400.0, 1.0, R, Q, 0.0)
    assert itm.Nd1 == 1.0 and itm.Nd2 == 1.0
    otm = d1d2(350.0, 400.0, 1.0, R, Q, 0.0)
    assert otm.Nd1 == 0.0 and otm.Nd2 == 0.0


def test_invalid_spot_strike_raises():
    with pytest.raises(ValueError):
        d1d2(-1.0, 400.0, 1.0, R, Q, 0.46)
    with pytest.raises(ValueError):
        price(389.80, 0.0, 1.0, R, Q, 0.46, Right.CALL)


def test_stock_right_rejected_by_price():
    with pytest.raises(ValueError):
        price(389.80, 460.0, 1.0, R, Q, 0.46, Right.STOCK)


# ---------------------------------------------------------------------------
# Fuzz: no NaN/inf across the whole parameter box
# ---------------------------------------------------------------------------


def test_fuzz_no_nan_inf_in_price_or_greeks():
    rng = random.Random(424242)
    for _ in range(800):
        S = rng.uniform(1.0, 5000.0)
        K = rng.uniform(1.0, 5000.0)
        T = rng.uniform(1.0 / 365.0, 3.0)
        sigma = rng.uniform(0.01, 3.0)
        q = rng.uniform(0.0, 0.1)
        for right in (Right.CALL, Right.PUT):
            px = price(S, K, T, R, q, sigma, right)
            assert math.isfinite(px) and px >= -1e-9
            g = all_greeks(S, K, T, R, q, sigma, right)
            for v in (g.delta, g.gamma, g.theta_day, g.theta_yr, g.vega, g.rho,
                      g.vanna, g.vomma, g.veta, g.ultima, g.charm_day,
                      g.speed, g.color_day, g.zomma):
                assert v is not None and math.isfinite(v)
