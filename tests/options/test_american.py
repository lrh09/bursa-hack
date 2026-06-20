"""Lock the CRR binomial tree (American + European limit), discrete-dividend
pricing, and the finite-difference greek harness.

Goldens / properties (math in comments):
  - CRR -> BSM in the European limit (avg of N, N+1 at N=1000, abs tol 1e-2).
  - American put >= European put (positive r => early-exercise premium).
  - American call == European call when q=0 / no dividends (no early exercise).
  - Single discrete div: escrowed European ~ continuous-yield PV adjustment.
  - American call with a juicy dividend shows positive early-exercise value.
  - q > 0 together with a discrete schedule raises ValueError.
Market state MS_A: S=389.80, sigma=0.46, T=1, r=0.045, q=0.
"""
from __future__ import annotations

import math

import pytest

from bursahack.options.american import (
    Greeks,
    Style,
    cross_check,
    crr_greeks,
    crr_price,
    early_exercise_boundary,
    fd_greek,
    price_discrete_div,
    pv_dividends,
)
from bursahack.options.bs import Right, all_greeks, first_order, price

R = 0.045
Q = 0.0
S, K, T, SIGMA = 389.80, 460.0, 1.0, 0.46


# ---------------------------------------------------------------------------
# European limit: CRR -> BSM
# ---------------------------------------------------------------------------


def test_crr_converges_to_bsm_european_call():
    # Average of N and N+1 damps the CRR odd/even oscillation.
    bsm = price(S, K, T, R, Q, SIGMA, Right.CALL)
    c_n = crr_price(S, K, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, N=1000)
    c_n1 = crr_price(S, K, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, N=1001)
    avg = 0.5 * (c_n + c_n1)
    assert math.isclose(avg, bsm, abs_tol=1e-2)


def test_crr_converges_to_bsm_european_put():
    bsm = price(S, K, T, R, Q, SIGMA, Right.PUT)
    c_n = crr_price(S, K, T, R, Q, SIGMA, Right.PUT, Style.EUROPEAN, N=1000)
    c_n1 = crr_price(S, K, T, R, Q, SIGMA, Right.PUT, Style.EUROPEAN, N=1001)
    avg = 0.5 * (c_n + c_n1)
    assert math.isclose(avg, bsm, abs_tol=1e-2)


def test_crr_360_call_reproduces_golden():
    # 360C @ MS_A = 91.96; tree (European) must hit it to a cent at high N.
    c_n = crr_price(S, 360.0, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, N=2000)
    c_n1 = crr_price(S, 360.0, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, N=2001)
    assert math.isclose(0.5 * (c_n + c_n1), 91.96, abs_tol=0.01)


# ---------------------------------------------------------------------------
# Early-exercise structure
# ---------------------------------------------------------------------------


def test_american_put_ge_european_put():
    # With r > 0 the American put carries an early-exercise premium.
    euro = price(S, K, T, R, Q, SIGMA, Right.PUT)
    amer = crr_price(S, K, T, R, Q, SIGMA, Right.PUT, Style.AMERICAN, N=1000)
    assert amer >= euro - 1e-9
    assert amer > euro + 1e-3  # the premium is materially positive here


def test_american_call_equals_european_when_no_dividend():
    # q = 0 and no dividends => never optimal to exercise a call early.
    euro = price(S, K, T, R, Q, SIGMA, Right.CALL)
    amer = crr_price(S, K, T, R, Q, SIGMA, Right.CALL, Style.AMERICAN, N=1000)
    assert math.isclose(amer, euro, abs_tol=0.05)


def test_american_value_ge_intrinsic():
    # Deep-ITM American put never worth less than intrinsic.
    deep = crr_price(300.0, 460.0, T, R, Q, SIGMA, Right.PUT, Style.AMERICAN, N=500)
    assert deep >= (460.0 - 300.0) - 1e-6


def test_crr_zero_time_is_intrinsic():
    assert math.isclose(
        crr_price(500.0, 460.0, 0.0, R, Q, SIGMA, Right.CALL, Style.AMERICAN, N=100),
        40.0,
    )
    assert math.isclose(
        crr_price(400.0, 460.0, 0.0, R, Q, SIGMA, Right.PUT, Style.AMERICAN, N=100),
        60.0,
    )


# ---------------------------------------------------------------------------
# Discrete dividends
# ---------------------------------------------------------------------------


def test_pv_dividends():
    # PV = sum(amt * e^(-r t)): div 5.0 @ t=0.5 -> 5 * e^(-0.045*0.5)
    sched = ((0.5, 5.0),)
    assert math.isclose(pv_dividends(sched, R), 5.0 * math.exp(-R * 0.5), rel_tol=1e-12)
    # two dividends sum
    sched2 = ((0.25, 2.0), (0.75, 3.0))
    expected = 2.0 * math.exp(-R * 0.25) + 3.0 * math.exp(-R * 0.75)
    assert math.isclose(pv_dividends(sched2, R), expected, rel_tol=1e-12)


def test_escrowed_european_matches_pv_adjusted_bsm():
    # Escrowed = BSM on S' = S - PV(divs). Single div 5.0 @ 0.5.
    sched = ((0.5, 5.0),)
    px, method = price_discrete_div(
        S, K, T, R, 0.0, SIGMA, Right.CALL, Style.EUROPEAN, sched, "escrowed"
    )
    s_adj = S - pv_dividends(sched, R)
    expected = price(s_adj, K, T, R, 0.0, SIGMA, Right.CALL)
    assert method == "escrowed"
    assert math.isclose(px, expected, rel_tol=1e-12)


def test_escrowed_div_close_to_continuous_yield_equivalent():
    # Discrete-div European ~ continuous-yield BSM with q_equiv = -ln(S'/S)/T.
    sched = ((0.5, 5.0),)
    px, _ = price_discrete_div(
        S, K, T, R, 0.0, SIGMA, Right.CALL, Style.EUROPEAN, sched, "escrowed"
    )
    s_adj = S - pv_dividends(sched, R)
    q_equiv = -math.log(s_adj / S) / T
    cont = price(S, K, T, R, q_equiv, SIGMA, Right.CALL)
    assert math.isclose(px, cont, abs_tol=1.0)


def test_american_call_with_dividend_has_early_exercise_premium():
    # A juicy dividend (15.0 @ 0.5) makes early exercise of an ITM American call
    # worthwhile -> American (CRR) > European (escrowed).
    sched = ((0.5, 15.0),)
    amer, m_a = price_discrete_div(
        S, 360.0, T, R, 0.0, SIGMA, Right.CALL, Style.AMERICAN, sched, "crr", N=700
    )
    euro, m_e = price_discrete_div(
        S, 360.0, T, R, 0.0, SIGMA, Right.CALL, Style.EUROPEAN, sched, "escrowed"
    )
    assert m_a == "crr" and m_e == "escrowed"
    assert amer > euro  # positive early-exercise value


def test_continuous_q_with_schedule_raises():
    sched = ((0.5, 5.0),)
    with pytest.raises(ValueError):
        price_discrete_div(
            S, K, T, R, 0.02, SIGMA, Right.CALL, Style.EUROPEAN, sched, "escrowed"
        )


def test_empty_schedule_defers_to_plain_tree():
    px, method = price_discrete_div(
        S, K, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, (), "escrowed", N=500
    )
    plain = crr_price(S, K, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, N=500)
    assert math.isclose(px, plain, rel_tol=1e-12)


def test_bad_method_raises():
    sched = ((0.5, 5.0),)
    with pytest.raises(ValueError):
        price_discrete_div(
            S, K, T, R, 0.0, SIGMA, Right.CALL, Style.EUROPEAN, sched, "nonsense"
        )


# ---------------------------------------------------------------------------
# Finite-difference harness vs analytic (numgreeks role)
# ---------------------------------------------------------------------------


def _bs_pricer(**pp: float) -> float:
    return price(pp["S"], pp["K"], pp["T"], pp["r"], pp["q"], pp["sigma"], pp["right"])


def test_fd_greek_matches_analytic_first_order():
    params = dict(S=S, K=K, T=T, r=R, q=Q, sigma=SIGMA, right=Right.CALL)
    fo = first_order(S, K, T, R, Q, SIGMA, Right.CALL)
    assert cross_check(fo["delta"], fd_greek(_bs_pricer, params, "delta"))[0]
    assert cross_check(fo["gamma"], fd_greek(_bs_pricer, params, "gamma"))[0]
    assert cross_check(fo["vega"], fd_greek(_bs_pricer, params, "vega") / 100.0)[0]
    assert cross_check(fo["rho"], fd_greek(_bs_pricer, params, "rho") / 100.0)[0]
    assert cross_check(fo["theta_yr"], fd_greek(_bs_pricer, params, "theta"))[0]


def test_fd_greek_unknown_greek_raises():
    params = dict(S=S, K=K, T=T, r=R, q=Q, sigma=SIGMA, right=Right.CALL)
    with pytest.raises(ValueError):
        fd_greek(_bs_pricer, params, "bogus")


def test_fd_greek_rejects_unexpected_param_keys():
    params = dict(S=S, K=K, T=T, r=R, q=Q, sigma=SIGMA, right=Right.CALL, junk=1)
    with pytest.raises(ValueError):
        fd_greek(_bs_pricer, params, "delta")


def test_cross_check_returns_gap():
    ok, gap = cross_check(1.000000, 1.000001, tol=1e-4)
    assert ok
    assert math.isclose(gap, 1e-6, rel_tol=1e-3)
    ok2, gap2 = cross_check(1.0, 2.0, tol=1e-4)
    assert not ok2
    assert math.isclose(gap2, 1.0, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Tree greeks (delta/gamma/theta from nodes; higher order via FD)
# ---------------------------------------------------------------------------


def test_crr_greeks_delta_gamma_match_bsm_european():
    # For a European-style tree at high N, tree-node delta/gamma ~ analytic BSM.
    cg = crr_greeks(S, K, T, R, Q, SIGMA, Right.CALL, Style.EUROPEAN, N=800)
    ag = all_greeks(S, K, T, R, Q, SIGMA, Right.CALL)
    assert math.isclose(cg.delta, ag.delta, abs_tol=2e-3)
    assert math.isclose(cg.gamma, ag.gamma, abs_tol=5e-4)
    assert math.isclose(cg.theta_yr, ag.theta_yr, abs_tol=0.5)
    # vega via FD-on-tree carries discretization noise -> looser tol.
    assert math.isclose(cg.vega, ag.vega, abs_tol=0.1)


def test_crr_greeks_returns_full_surface_with_higher_orders():
    cg = crr_greeks(S, K, T, R, Q, SIGMA, Right.CALL, Style.AMERICAN, N=400)
    assert isinstance(cg, Greeks)
    # higher-order greeks are filled via fd_greek (not None)
    for field in ("vanna", "vomma", "charm_day", "speed", "zomma", "color_day"):
        assert getattr(cg, field) is not None


# ---------------------------------------------------------------------------
# Early-exercise boundary
# ---------------------------------------------------------------------------


def test_early_exercise_boundary_put_is_below_strike_and_monotone_ish():
    bnd = early_exercise_boundary(S, K, T, R, Q, SIGMA, Right.PUT, N=300)
    assert len(bnd) > 0
    # all critical prices for a put are below the strike (exercise when deep ITM)
    for t, sstar in bnd:
        assert 0.0 <= t <= T
        assert sstar < K
    # boundary is returned in ascending time
    times = [t for t, _ in bnd]
    assert times == sorted(times)


def test_early_exercise_boundary_empty_for_degenerate():
    assert early_exercise_boundary(S, K, 0.0, R, Q, SIGMA, Right.PUT, N=100) == []
    assert early_exercise_boundary(S, K, T, R, Q, 0.0, Right.PUT, N=100) == []
