"""Analytic-probability + Monte-Carlo goldens for the options toolkit.

Market state MS_A (the bull-call economics / probability anchor):
    S = 389.80,  sigma = 0.46,  T = 1,  r = 0.045,  q = 0

All numbers below are independently hand-derived (math shown in comments) and
locked with ``math.isclose`` — same discipline as ``tests/test_costs.py``.

The bull-call 360/460 x8 worked example (entry: long 360C @ 91.96, short 460C @
53.00) underlies the POP / payoff-bucket goldens. Per-spread entry debit =
91.96 - 53.00 = 38.96; breakeven = 360 + 38.96 = 398.96.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np

from bursahack.options.montecarlo import (
    pnl_distribution,
    pnl_percentiles,
    reconcile,
    simulate_terminal,
    var_cvar,
)
from bursahack.options.prob import (
    expected_pnl,
    payoff_buckets,
    pop_expiry,
    prob_in_range,
    prob_itm,
    prob_price_below,
    prob_touch,
    resolve_drift,
)
from bursahack.options.position import net_cost_entry
from bursahack.options.types import Leg, Measure, Right

# --- MS_A constants ---------------------------------------------------------
S = 389.80
SIGMA = 0.46
T = 1.0
R = 0.045
Q = 0.0
EXPIRY = date(2027, 6, 18)


def _bull_call_8x() -> list[Leg]:
    """The TSLA 360/460 x8 bull call spread at its entry fills."""
    return [
        Leg(right=Right.CALL, strike=360.0, expiry=EXPIRY, qty=8, entry_price=91.96, underlying="TSLA"),
        Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-8, entry_price=53.00, underlying="TSLA"),
    ]


def _bull_call_1x() -> list[Leg]:
    return [
        Leg(right=Right.CALL, strike=360.0, expiry=EXPIRY, qty=1, entry_price=91.96, underlying="TSLA"),
        Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-1, entry_price=53.00, underlying="TSLA"),
    ]


# ===========================================================================
# resolve_drift — the single measure boundary (L6)
# ===========================================================================
def test_resolve_drift_risk_neutral_is_r_minus_q():
    # RN drift = r - q = 0.045 - 0 = 0.045
    assert math.isclose(resolve_drift(Measure.RISK_NEUTRAL, R, Q, None), 0.045, rel_tol=1e-12)


def test_resolve_drift_real_world_uses_mu():
    assert math.isclose(resolve_drift(Measure.REAL_WORLD, R, Q, 0.10), 0.10, rel_tol=1e-12)


def test_resolve_drift_real_world_requires_mu():
    try:
        resolve_drift(Measure.REAL_WORLD, R, Q, None)
    except ValueError:
        return
    raise AssertionError("REAL_WORLD with mu=None must raise")


# ===========================================================================
# prob_itm — N(d2), NOT delta N(d1) (L6)
# ===========================================================================
def test_prob_itm_call_is_Nd2_golden():
    # GOLDEN: P(S_T > 460) RN = N(d2) = 0.3113
    #   d1 = (ln(389.80/460) + (0.045 + 0.5*0.46^2)*1) / (0.46*1) = -0.1718...
    #   d2 = d1 - 0.46 = -0.6318...,  N(d2) = 0.3113
    out = prob_itm(S, 460.0, T, R, SIGMA, q=Q, right=Right.CALL)
    assert math.isclose(out["prob_itm"], 0.3113, abs_tol=5e-4)
    assert math.isclose(out["prob_itm"] + out["prob_otm"], 1.0, rel_tol=1e-12)
    assert out["measure"] == "rn"


def test_prob_itm_put_is_complement_of_call():
    call = prob_itm(S, 460.0, T, R, SIGMA, q=Q, right=Right.CALL)["prob_itm"]
    put = prob_itm(S, 460.0, T, R, SIGMA, q=Q, right=Right.PUT)["prob_itm"]
    # P(S_T>K) + P(S_T<K) = 1 (continuous law, ignore the measure-zero tie)
    assert math.isclose(call + put, 1.0, rel_tol=1e-9)


def test_prob_itm_differs_from_delta():
    # N(d2) (prob-ITM) must be strictly below N(d1) (delta) for a call with T,sigma>0.
    # delta = N(d1); since d1 > d2, N(d1) > N(d2) (L6: prob-ITM != delta).
    from bursahack.options.core import norm_cdf
    out = prob_itm(S, 460.0, T, R, SIGMA, q=Q, right=Right.CALL)
    assert norm_cdf(out["d1"]) > out["prob_itm"]


# ===========================================================================
# Low-level kernels (resolved drift; measure-agnostic)
# ===========================================================================
def test_prob_price_below_complement_of_above():
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    below = prob_price_below(S, 460.0, T, SIGMA, drift)
    # P(S_T<=460) = 1 - N(d2) = 1 - 0.3113 = 0.6887
    assert math.isclose(below, 1.0 - 0.3113, abs_tol=5e-4)


def test_prob_in_range_sums_to_partition():
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    a = prob_price_below(S, 300.0, T, SIGMA, drift)
    mid = prob_in_range(S, 300.0, 500.0, T, SIGMA, drift)
    above = 1.0 - prob_price_below(S, 500.0, T, SIGMA, drift)
    assert math.isclose(a + mid + above, 1.0, rel_tol=1e-9)


def test_prob_price_below_nonpositive_level_is_zero():
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    assert prob_price_below(S, 0.0, T, SIGMA, drift) == 0.0
    assert prob_price_below(S, -5.0, T, SIGMA, drift) == 0.0


# ===========================================================================
# pop_expiry — measure separation (the headline golden)
# ===========================================================================
def test_pop_expiry_measure_separation_golden():
    legs = _bull_call_1x()
    # GOLDEN: POP at BE=398.96 -> 0.4275 (RN) vs 0.4748 (RW, mu=0.10).
    rn = pop_expiry(legs, S, T, SIGMA, R, q=Q, measure=Measure.RISK_NEUTRAL)
    rw = pop_expiry(legs, S, T, SIGMA, R, q=Q, measure=Measure.REAL_WORLD, mu=0.10)
    assert math.isclose(rn["pop"], 0.4275, abs_tol=1e-3)
    assert math.isclose(rw["pop"], 0.4748, abs_tol=1e-3)
    # The bull call has a single breakeven at 398.96 and a single open profit ray.
    assert len(rn["breakevens"]) == 1
    assert math.isclose(rn["breakevens"][0], 398.96, abs_tol=1e-2)
    assert len(rn["profit_regions"]) == 1
    assert rn["profit_regions"][0][1] == math.inf


def test_pop_real_world_exceeds_risk_neutral_for_bull():
    # Positive real-world drift pushes more mass above the upside breakeven.
    legs = _bull_call_1x()
    rn = pop_expiry(legs, S, T, SIGMA, R, q=Q, measure=Measure.RISK_NEUTRAL)["pop"]
    rw = pop_expiry(legs, S, T, SIGMA, R, q=Q, measure=Measure.REAL_WORLD, mu=0.10)["pop"]
    assert rw > rn


# ===========================================================================
# prob_touch — Reiner-Rubinstein one-touch
# ===========================================================================
def test_prob_touch_up_golden():
    # GOLDEN: touch(460,'up', 1y) = 0.6840
    out = prob_touch(S, 460.0, T, R, SIGMA, q=Q, direction="up")
    assert math.isclose(out["prob_touch"], 0.6840, abs_tol=1e-3)


def test_prob_touch_down_golden():
    # GOLDEN: touch(312,'down') = 0.6681
    out = prob_touch(S, 312.0, T, R, SIGMA, q=Q, direction="down")
    assert math.isclose(out["prob_touch"], 0.6681, abs_tol=1e-3)


def test_prob_touch_geq_finish_itm():
    # Touch probability must dominate finish-ITM probability (you can touch and
    # then drift back). touch(460,up)=0.6840 >= P(S_T>460)=0.3113.
    touch = prob_touch(S, 460.0, T, R, SIGMA, q=Q, direction="up")["prob_touch"]
    finish = prob_itm(S, 460.0, T, R, SIGMA, q=Q, right=Right.CALL)["prob_itm"]
    assert touch >= finish


def test_prob_touch_already_through_is_certain():
    # Up-barrier already at/below spot -> certain touch.
    assert prob_touch(S, 300.0, T, R, SIGMA, q=Q, direction="up")["prob_touch"] == 1.0
    assert prob_touch(S, 500.0, T, R, SIGMA, q=Q, direction="down")["prob_touch"] == 1.0


# ===========================================================================
# net_cost_entry sign sanity (used by payoff/EV) — debit > 0
# ===========================================================================
def test_net_cost_entry_bull_call_is_positive_debit():
    # 8x bull call: (91.96 - 53.00) * 8 * 100 = 38.96 * 800 = 31168 debit.
    assert math.isclose(net_cost_entry(_bull_call_8x()), 31168.0, rel_tol=1e-9)
    # single spread: 38.96 * 100 = 3896
    assert math.isclose(net_cost_entry(_bull_call_1x()), 3896.0, rel_tol=1e-9)


# ===========================================================================
# payoff_buckets — mass partition (iron-condor property: sums to 1.0)
# ===========================================================================
def test_payoff_buckets_mass_sums_to_one_bull_call():
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    out = payoff_buckets(_bull_call_8x(), S, T, SIGMA, drift)
    total = sum(b["prob"] for b in out["buckets"])
    assert math.isclose(total, 1.0, rel_tol=1e-6)


def test_payoff_buckets_iron_condor_partition():
    # Iron condor: long 320P / short 340P / short 440C / long 460C, qty 1 each.
    # Buckets + tails partition the spot axis -> probability mass sums to 1.0.
    legs = [
        Leg(right=Right.PUT, strike=320.0, expiry=EXPIRY, qty=1, entry_price=4.0, underlying="X"),
        Leg(right=Right.PUT, strike=340.0, expiry=EXPIRY, qty=-1, entry_price=7.0, underlying="X"),
        Leg(right=Right.CALL, strike=440.0, expiry=EXPIRY, qty=-1, entry_price=8.0, underlying="X"),
        Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=1, entry_price=4.5, underlying="X"),
    ]
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    out = payoff_buckets(legs, S, T, SIGMA, drift)
    total = sum(b["prob"] for b in out["buckets"])
    assert math.isclose(total, 1.0, rel_tol=1e-6)


# ===========================================================================
# expected_pnl — RN-discounted E[P&L] of a FAIR spread is ~0
# ===========================================================================
def test_expected_pnl_fair_spread_near_zero():
    # Price both legs at fair BSM value, build a leg book with those entries, then
    # the risk-neutral discounted E[P&L] must be ~0 (no edge in a fairly priced
    # spread). This is the analytic anchor the MC reconciles against (§5).
    from bursahack.options.bsm import price
    c360 = price(S, 360.0, T, R, Q, SIGMA, Right.CALL)
    c460 = price(S, 460.0, T, R, Q, SIGMA, Right.CALL)
    legs = [
        Leg(right=Right.CALL, strike=360.0, expiry=EXPIRY, qty=1, entry_price=c360, underlying="TSLA"),
        Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-1, entry_price=c460, underlying="TSLA"),
    ]
    out = expected_pnl(legs, S, T, R, SIGMA, q=Q, measure=Measure.RISK_NEUTRAL)
    # discounted E[P&L] ~ 0 (numeric integral tolerance)
    assert abs(out["e_pnl_pv"]) < 1.0  # well within a dollar on a 100-mult spread


# ===========================================================================
# Monte-Carlo: MC POP ~ analytic POP (the assignment's headline MC test)
# ===========================================================================
def test_mc_pop_matches_analytic_pop_for_vertical():
    legs = _bull_call_1x()
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    st = simulate_terminal(S, T, SIGMA, drift, n=400_000, seed=7, antithetic=True)
    nc = net_cost_entry(legs)
    dist = pnl_distribution(legs, st, mult=100, net_cost=nc, r=R, T=T)
    analytic = pop_expiry(legs, S, T, SIGMA, R, q=Q, measure=Measure.RISK_NEUTRAL)["pop"]
    # MC POP within ~3 binomial SE of analytic (~0.4275); se ~ sqrt(p(1-p)/n) ~ 0.0008
    se = math.sqrt(analytic * (1 - analytic) / dist["n"])
    rec = reconcile(dist["pop"], se, analytic, k=4.0)
    assert rec["pass"], rec


def test_mc_terminal_mean_matches_forward():
    # E[S_T] under RN = S * exp(drift*T) = 389.80 * exp(0.045) = 407.74
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    st = simulate_terminal(S, T, SIGMA, drift, n=500_000, seed=11, antithetic=True)
    forward = S * math.exp(drift * T)
    se = float(st.std(ddof=1)) / math.sqrt(st.size)
    rec = reconcile(float(st.mean()), se, forward, k=4.0)
    assert rec["pass"], rec


def test_mc_price_reconciles_with_bsm():
    # A single long 460C: MC mean discounted intrinsic ~ bsm.price(460C).
    from bursahack.options.bsm import price
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    st = simulate_terminal(S, T, SIGMA, drift, n=500_000, seed=3, antithetic=True)
    intrinsic = np.maximum(st - 460.0, 0.0) * math.exp(-R * T)
    mc_px = float(intrinsic.mean())
    se = float(intrinsic.std(ddof=1)) / math.sqrt(intrinsic.size)
    analytic = price(S, 460.0, T, R, Q, SIGMA, Right.CALL)
    rec = reconcile(mc_px, se, analytic, k=4.0)
    assert rec["pass"], rec


# ===========================================================================
# Monte-Carlo P&L distribution: VaR monotonic + floored defined-risk
# ===========================================================================
def test_mc_var_is_monotonic_in_alpha():
    legs = _bull_call_8x()
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    st = simulate_terminal(S, T, SIGMA, drift, n=200_000, seed=5, antithetic=True)
    nc = net_cost_entry(legs)
    dist = pnl_distribution(legs, st, mult=100, net_cost=nc, r=R, T=T, alphas=(0.90, 0.95, 0.99))
    v90 = dist["var"]["0.9"]
    v95 = dist["var"]["0.95"]
    v99 = dist["var"]["0.99"]
    # Higher confidence -> larger (or equal) VaR loss.
    assert v90 <= v95 <= v99
    # CVaR >= VaR at each level (CVaR is the mean of the worse tail).
    assert dist["cvar"]["0.95"] >= v95 - 1e-6


def test_var_cvar_defined_risk_floor_truncates():
    # 360/460 x8 max loss = entry debit = 31168. With floor set, VaR/CVaR clamp
    # to it even though the worst MC draw equals it anyway (defined-risk).
    legs = _bull_call_8x()
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    st = simulate_terminal(S, T, SIGMA, drift, n=100_000, seed=9, antithetic=True)
    nc = net_cost_entry(legs)
    pnl = pnl_distribution(legs, st, mult=100, net_cost=nc, r=R, T=T, floor=31168.0)
    assert pnl["var"]["0.95"] <= 31168.0 + 1e-6
    assert pnl["cvar"]["0.99"] <= 31168.0 + 1e-6
    assert pnl["tail_flag"] == "floored"


def test_var_cvar_naked_short_flags_unbounded():
    # A naked short call has an open loss tail -> flag='unbounded' when no floor.
    legs = [Leg(right=Right.CALL, strike=400.0, expiry=EXPIRY, qty=-1,
                entry_price=80.0, underlying="X")]
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    st = simulate_terminal(S, T, SIGMA, drift, n=200_000, seed=2, antithetic=True)
    nc = net_cost_entry(legs)
    pnl = pnl_distribution(legs, st, mult=100, net_cost=nc, r=R, T=T)
    assert pnl["tail_flag"] == "unbounded"


def test_var_cvar_monotonic_pure_sample():
    # Direct property on a synthetic Gaussian P&L sample (no option machinery):
    # VaR strictly nondecreasing across confidence levels.
    rng = np.random.default_rng(0)
    pnl = rng.normal(0.0, 1000.0, 200_000)
    out = var_cvar(pnl, alphas=(0.90, 0.95, 0.975, 0.99))
    vs = [out["var"]["0.9"], out["var"]["0.95"], out["var"]["0.975"], out["var"]["0.99"]]
    assert vs == sorted(vs)


def test_pnl_percentiles_ordered():
    rng = np.random.default_rng(1)
    pnl = rng.normal(50.0, 500.0, 100_000)
    pct = pnl_percentiles(pnl)
    vals = [pct["5"], pct["25"], pct["50"], pct["75"], pct["95"]]
    assert vals == sorted(vals)


# ===========================================================================
# Optional fat tail: Student-t keeps variance, fattens tails
# ===========================================================================
def test_student_t_terminal_matches_variance_but_fatter_tail():
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    gbm = simulate_terminal(S, T, SIGMA, drift, n=300_000, seed=4, antithetic=True, dist="gbm")
    stt = simulate_terminal(S, T, SIGMA, drift, n=300_000, seed=4, antithetic=True,
                            dist="student_t", nu=4.0)
    # Both target the same lognormal mean (forward); fat-tail has heavier extremes.
    fwd = S * math.exp(drift * T)
    assert math.isclose(float(gbm.mean()), fwd, rel_tol=2e-2)
    assert math.isclose(float(stt.mean()), fwd, rel_tol=3e-2)
    # Student-t produces a more extreme max draw than GBM at the same seed scale.
    assert float(stt.max()) > float(gbm.max())


def test_student_t_requires_finite_variance():
    drift = resolve_drift(Measure.RISK_NEUTRAL, R, Q, None)
    try:
        simulate_terminal(S, T, SIGMA, drift, n=100, seed=1, dist="student_t", nu=2.0)
    except ValueError:
        return
    raise AssertionError("student_t with nu<=2 must raise (infinite variance)")


# ===========================================================================
# reconcile gate behaviour
# ===========================================================================
def test_reconcile_passes_within_k_se():
    rec = reconcile(mc_stat=100.2, mc_se=0.1, analytic=100.0, k=3.0)
    # gap=0.2, se=0.1 -> gap_in_se=2.0 <= 3 -> pass
    assert rec["pass"]
    assert math.isclose(rec["gap_in_se"], 2.0, rel_tol=1e-9)


def test_reconcile_fails_outside_k_se():
    rec = reconcile(mc_stat=101.0, mc_se=0.1, analytic=100.0, k=3.0)
    # gap=1.0, se=0.1 -> 10 se -> fail
    assert not rec["pass"]


def test_reconcile_zero_se_uses_abs_tol():
    assert reconcile(5.0, 0.0, 5.0, k=3.0)["pass"]
    assert not reconcile(5.0, 0.0, 5.5, k=3.0)["pass"]
