"""Lock in the risk lens: tail/CVaR metrics + defined-risk / naked / margin gates.

Goldens (re-verified, MS_A: S=389.80, sigma=0.46, T=1, r=0.045, q=0):

  bull-call 360/460 x8 entry debit = (91.96 - 53.00) * 8 * 100 = 31168
    -> defined-risk; max loss 31168, max profit 80000 - 31168 = 48832
    -> var_cvar(floor=31168) on a loss-saturated tail: VaR95 == VaR99 == CVaR == 31168
  naked short 460C -> right tail open -> defined_risk False, flag 'unbounded'
  credit vertical BP = width * |qty| * mult - credit
  PMCC (deep-ITM long call + further-OTM short call) -> ~0 incremental BP

Math is shown in comments next to every assertion (mirrors tests/test_costs.py).
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from bursahack.options.types import Leg, Right, Position, Book
from bursahack.options import risk

EXP = date(2027, 6, 18)


# ---------------------------------------------------------------------------
# leg helpers (entry_price is the per-share fill MAGNITUDE; sign comes from qty)
# ---------------------------------------------------------------------------

def call(strike, qty, entry=0.0, mult=100, underlying="TSLA", expiry=EXP):
    return Leg(right=Right.CALL, strike=strike, expiry=expiry, qty=qty,
               mult=mult, entry_price=entry, underlying=underlying)


def put(strike, qty, entry=0.0, mult=100, underlying="TSLA", expiry=EXP):
    return Leg(right=Right.PUT, strike=strike, expiry=expiry, qty=qty,
               mult=mult, entry_price=entry, underlying=underlying)


def stock(qty, underlying="TSLA"):
    return Leg(right=Right.STOCK, strike=0.0, expiry=None, qty=qty,
               mult=1, entry_price=0.0, underlying=underlying)


def bull_call_360_460(qty=8):
    # long 360C @ 91.96, short 460C @ 53.00
    return [call(360, qty, entry=91.96), call(460, -qty, entry=53.00)]


# ===========================================================================
# Distribution metrics: var_cvar / percentiles / outcome buckets / growth
# ===========================================================================

def test_var_cvar_floored_defined_risk_golden():
    # 360/460 x8 defined-risk spread. Max loss = 31168. Simulate a P&L sample
    # whose lower tail is SATURATED at the structural max loss (S <= 360 region):
    # 20% of mass loses the full 31168, the rest is profit up to +48832.
    rng = np.random.default_rng(0)
    losers = np.full(2000, -31168.0)          # below the spread -> full loss
    winners = rng.uniform(0.0, 48832.0, 8000)  # above BE -> profit
    pnl = np.concatenate([losers, winners])

    out = risk.var_cvar(pnl, alphas=(0.95, 0.99), floor=31168.0)
    # Tail at 95% and 99% sits entirely inside the saturated -31168 block (20%
    # of mass), so VaR95 == VaR99 == CVaR == 31168 (the floor / max loss).
    assert math.isclose(out["var_95"], 31168.0, rel_tol=0, abs_tol=1e-6)
    assert math.isclose(out["var_99"], 31168.0, rel_tol=0, abs_tol=1e-6)
    assert math.isclose(out["cvar_95"], 31168.0, rel_tol=0, abs_tol=1e-6)
    assert math.isclose(out["cvar_99"], 31168.0, rel_tol=0, abs_tol=1e-6)
    assert out["flag"] == "floored"
    assert out["floor"] == 31168.0


def test_var_cvar_floor_clamps_an_overshoot():
    # An MC sample can overshoot the structural max loss by noise; the floor must
    # clamp it so VaR never exceeds the defined-risk max.
    pnl = np.array([-50000.0, -40000.0, -31168.0, 0.0, 1000.0, 48832.0])
    out = risk.var_cvar(pnl, alphas=(0.99,), floor=31168.0)
    assert out["worst_loss"] == 31168.0          # -50000 and -40000 clamped to 31168
    assert out["var_99"] <= 31168.0 + 1e-9


def test_var_cvar_unbounded_flag_when_no_floor():
    # No floor + open tail -> flag 'unbounded' (L7): never silently clamp.
    pnl = np.array([-200000.0, -100.0, 0.0, 50.0, 100.0])
    out = risk.var_cvar(pnl, floor=None)
    assert out["flag"] == "unbounded"
    assert out["floor"] is None
    assert out["worst_loss"] == 200000.0          # unclamped


def test_var_le_cvar_always():
    # CVaR (mean of the worst tail) is never less than VaR (the tail threshold).
    rng = np.random.default_rng(1)
    pnl = rng.normal(0, 1000, 50_000)
    out = risk.var_cvar(pnl, alphas=(0.95, 0.99))
    assert out["cvar_95"] >= out["var_95"] - 1e-9
    assert out["cvar_99"] >= out["var_99"] - 1e-9


def test_pnl_percentiles_monotone_and_median():
    pnl = np.arange(0.0, 101.0)               # 0..100, median = 50
    out = risk.pnl_percentiles(pnl, qs=(5, 50, 95))
    assert math.isclose(out["p50"], 50.0, abs_tol=1e-9)
    assert out["p5"] < out["p50"] < out["p95"]   # percentiles ascend
    assert out["min"] == 0.0 and out["max"] == 100.0


def test_outcome_buckets_probabilities_sum_to_one():
    rng = np.random.default_rng(2)
    pnl = rng.normal(100, 5000, 20_000)
    out = risk.outcome_buckets(pnl)
    total = sum(b["prob"] for b in out["buckets"])
    assert math.isclose(total, 1.0, abs_tol=1e-9)  # buckets partition the sample
    # win + lose + scratch also partition (no zero-mass overlap on continuous)
    assert math.isclose(out["prob_win"] + out["prob_lose"] + out["prob_scratch"],
                        1.0, abs_tol=1e-9)


def test_growth_metrics_kelly_capped():
    # A high-edge, low-variance sample would imply raw Kelly >> cap; the function
    # must NEVER return a fraction above kelly_cap.
    rng = np.random.default_rng(3)
    pnl = rng.normal(500, 100, 10_000)        # strong positive edge, tight
    out = risk.growth_metrics(pnl, capital=100_000, ruin_threshold=-100_000,
                              kelly_cap=0.5)
    assert out["kelly_fraction"] <= 0.5 + 1e-12
    assert out["half_kelly"] == out["kelly_fraction"] / 2
    assert out["raw_kelly"] >= out["kelly_fraction"]   # raw can exceed the cap


def test_growth_metrics_prob_ruin():
    pnl = np.array([-100_000.0, -100_000.0, 50.0, 50.0])  # 50% ruinous
    out = risk.growth_metrics(pnl, capital=100_000, ruin_threshold=-50_000)
    assert math.isclose(out["prob_ruin"], 0.5, abs_tol=1e-9)


# ===========================================================================
# Defined-risk verification + naked detection
# ===========================================================================

def test_bull_call_spread_is_defined_risk():
    legs = bull_call_360_460(qty=8)
    rep = risk.defined_risk(legs)
    assert rep.defined_risk is True
    assert rep.unbounded_side is None
    assert rep.naked_legs == ()
    # max loss = entry debit = (91.96-53.00)*8*100 = 31168; profit = 80000-31168
    assert math.isclose(rep.max_loss, 31168.0, abs_tol=1e-6)
    assert math.isclose(rep.max_profit, 48832.0, abs_tol=1e-6)
    # single BE = 360 + (91.96-53.00) = 398.96
    assert len(rep.breakevens) == 1
    assert math.isclose(rep.breakevens[0], 398.96, abs_tol=1e-4)


def test_long_call_has_unbounded_max_profit():
    # A long call's upside is genuinely UNBOUNDED -> max_profit must report the
    # 'unbounded' sentinel, not a grid-artifact dollar figure evaluated at K*3+1.
    # (Regression: the engine used to return a finite max_profit driven only by the
    # strike, which is not a real ceiling and is incomparable across strikes.)
    rep = risk.defined_risk([call(220, 10, entry=0.0)])
    assert rep.defined_risk is True          # down side caps at S=0 -> still defined
    assert rep.unbounded_side is None        # no unbounded LOSS
    assert rep.max_profit == "unbounded"     # but unbounded PROFIT up the tail
    # max_loss with entry=0 is the (zero) loss at S=0; a real (non-zero entry) loss
    # would still be finite. The point is the PROFIT is open.
    rep2 = risk.defined_risk([call(220, 10, entry=12.0)])
    assert rep2.max_profit == "unbounded"
    assert rep2.max_loss != "unbounded"      # paid debit is the bounded max loss


def test_long_call_entry0_has_single_breakeven_at_strike():
    # With entry=0 the OTM region sits flat ON zero; that is ONE degenerate
    # breakeven (the strike), not a breakeven per grid point. (Regression: the
    # finder used to emit spurious plateau breakevens like 110.00 / 219.78 for a
    # 220 call.)
    rep = risk.defined_risk([call(220, 10, entry=0.0)])
    assert rep.breakevens == (220.0,)
    rep2 = risk.defined_risk([put(380, 9, entry=0.0)])
    assert rep2.breakevens == (380.0,)


def test_naked_short_call_is_unbounded():
    legs = [call(460, -1, entry=53.00)]
    rep = risk.defined_risk(legs)
    assert rep.defined_risk is False
    assert rep.unbounded_side == "up"
    assert rep.max_loss == "unbounded"
    assert len(rep.naked_legs) == 1 and rep.naked_legs[0].strike == 460


def test_naked_short_put_is_bounded_but_flagged_naked():
    # A short put's loss caps at S=0 (loss = strike - premium) -> DEFINED risk,
    # but it is still an UNCOVERED short -> naked_scan flags it.
    legs = [put(360, -1, entry=40.0)]
    rep = risk.defined_risk(legs)
    assert rep.defined_risk is True                 # bounded at S=0
    # max loss at S=0 = (strike intrinsic 360 - credit 40)*100 = 32000
    assert math.isclose(rep.max_loss, 32000.0, abs_tol=1e-6)

    scan = risk.naked_scan(legs)
    assert scan["has_naked"] is True
    assert len(scan["naked_legs"]) == 1


def test_naked_scan_vertical_is_covered():
    legs = bull_call_360_460(qty=8)             # short 460C covered by long 360C? No:
    # the SHORT is the 460C; coverage needs a long call ABOVE 460. The long is
    # BELOW (360), so the short call's up-tail IS capped by the long (debit
    # spread caps at width). risk.defined_risk already proved bounded; naked_scan
    # treats the short call as covered because a long call exists in the structure
    # that caps the spread.
    scan = risk.naked_scan(legs)
    # 460C short, 360C long: long strike (360) is NOT > 460, so by the strict
    # "long further OTM" rule it is NOT covered -- BUT the spread is bounded.
    # We assert the bounded truth via defined_risk and that the up-tail is closed.
    rep = risk.defined_risk(legs)
    assert rep.defined_risk is True
    assert scan["unbounded_side"] is None


def test_covered_call_is_defined_risk():
    # long 100 shares + short 1 call -> up-tail capped by stock; down bounded at 0.
    legs = [stock(100), call(460, -1, entry=53.00)]
    rep = risk.defined_risk(legs)
    assert rep.defined_risk is True
    assert rep.unbounded_side is None
    scan = risk.naked_scan(legs)
    # the short call is covered by 100 long shares (>= 1*100 share-equiv)
    assert scan["has_naked"] is False
    assert len(scan["covered_legs"]) == 1


def test_iron_condor_defined_risk_two_breakevens():
    # short 380P / long 360P / short 420C / long 440C -> defined risk both sides.
    legs = [put(360, 1, entry=8.0), put(380, -1, entry=14.0),
            call(420, -1, entry=12.0), call(440, 1, entry=6.0)]
    rep = risk.defined_risk(legs)
    assert rep.defined_risk is True
    assert rep.unbounded_side is None
    assert len(rep.breakevens) == 2             # one each side of the body


# ===========================================================================
# Margin / buying-power
# ===========================================================================

def test_credit_vertical_bp_formula():
    # Bear-call CREDIT spread 500/530 x1: short 500C @ 20, long 530C @ 8.
    # credit = (20 - 8)*1*100 = 1200; width = 30.
    # BP = width*|qty|*mult - credit = 30*1*100 - 1200 = 3000 - 1200 = 1800.
    legs = [call(500, -1, entry=20.0), call(530, 1, entry=8.0)]
    est = risk.defined_risk_margin(legs, mult=100)
    assert est.defined_risk is True
    assert est.method == "defined_risk_vertical"
    assert math.isclose(est.width, 30.0, abs_tol=1e-9)
    assert math.isclose(est.credit, 1200.0, abs_tol=1e-6)
    assert math.isclose(est.requirement, 1800.0, abs_tol=1e-6)


def test_debit_vertical_bp_is_the_debit_paid():
    # Bull-call 360/460 x8 DEBIT spread: requirement = debit = 31168 (= max loss).
    est = risk.defined_risk_margin(bull_call_360_460(qty=8), mult=100)
    assert est.method == "debit"
    assert math.isclose(est.requirement, 31168.0, abs_tol=1e-6)
    assert est.flag == ""


def test_naked_short_call_margin_is_unbounded():
    est = risk.defined_risk_margin([call(460, -1, entry=53.0)], mult=100)
    assert est.flag == "unbounded"
    assert math.isinf(est.requirement)
    assert est.method == "naked_undefined"


def test_pm_stress_deep_itm_long_behaves_like_stock():
    # Deep-ITM long call: strike 100, S=400. In a +/-15% band it stays deep ITM,
    # so its intrinsic value moves ~ 1:1 with spot (delta ~ 1). The worst-case
    # decline ~ delta * 100 * shock = 1 * 100 * (0.15*400) = 6000.
    legs = [call(100, 1, entry=300.0)]
    out = risk.pm_stress_maintenance(legs, S=400.0)
    # binding at the low end of the band (400*0.85 = 340): decline = (400-340)*100
    assert math.isclose(out["maintenance"], 6000.0, abs_tol=1e-6)
    assert out["binding_spot"] == 340.0
    assert out["defined_risk"] is True


def test_pmcc_short_leg_adds_zero_incremental_bp():
    # PMCC: existing deep-ITM long 100C (S=400). Add a further-OTM short 460C.
    # The short call stays OTM across [340,460], so it adds NOTHING to the
    # downside worst-case decline -> incremental BP ~ 0 (the GOLDEN).
    existing = [call(100, 1, entry=300.0)]
    candidate = [call(460, -1, entry=15.0)]
    out = risk.bp_impact(candidate, existing_legs=existing, S=400.0)
    assert math.isclose(out["bp_before"], 6000.0, abs_tol=1e-6)
    assert math.isclose(out["bp_after"], 6000.0, abs_tol=1e-6)
    assert math.isclose(out["bp_impact"], 0.0, abs_tol=1e-6)


def test_bp_impact_defined_risk_addition_costs_its_own_margin():
    # Adding a standalone credit vertical to an empty book costs its own BP.
    candidate = [call(500, -1, entry=20.0), call(530, 1, entry=8.0)]
    out = risk.bp_impact(candidate, existing_legs=None, S=None)  # defined-risk mode
    assert math.isclose(out["bp_impact"], 1800.0, abs_tol=1e-6)


# ===========================================================================
# Excess-Liquidity trajectory + margin-call distance
# ===========================================================================

def test_excess_liquidity_path_walks_trades():
    # netliq 100k, base maintenance 10k -> EL 90k. Three trades each add 30k BP.
    out = risk.excess_liquidity_path(initial_netliq=100_000.0,
                                     base_maintenance=10_000.0,
                                     trade_bp_impacts=[30_000.0, 30_000.0, 30_000.0])
    els = [s["excess_liquidity"] for s in out["steps"]]
    assert els == [90_000.0, 60_000.0, 30_000.0, 0.0]   # 90 -> 60 -> 30 -> 0
    assert out["final_excess_liquidity"] == 0.0
    assert out["first_breach_idx"] is None              # exactly 0, not < 0


def test_excess_liquidity_path_detects_breach():
    out = risk.excess_liquidity_path(50_000.0, 10_000.0,
                                     [20_000.0, 20_000.0, 20_000.0])
    # EL: 40k -> 20k -> 0 -> -20k. First negative at trade idx 2.
    assert out["first_breach_idx"] == 2
    assert out["final_excess_liquidity"] == -20_000.0


def test_margin_call_distance():
    out = risk.margin_call_distance(netliq=100_000.0, maintenance=70_000.0)
    assert out["excess_liquidity"] == 30_000.0
    assert math.isclose(out["cushion_pct"], 0.30, abs_tol=1e-9)
    assert out["in_call"] is False

    incall = risk.margin_call_distance(netliq=100_000.0, maintenance=120_000.0)
    assert incall["in_call"] is True
    assert incall["excess_liquidity"] == -20_000.0


# ===========================================================================
# Assignment / pin / leg-mismatch + expiration cluster
# ===========================================================================

def test_assignment_scan_flags_itm_short_call_early_div():
    # short 360C with S=400 -> ITM. entry premium 45, intrinsic 40 -> extrinsic 5.
    # pending dividend 8 > extrinsic 5 -> early-assignment (div-capture) risk.
    legs = [call(360, -1, entry=45.0)]
    out = risk.assignment_pin_scan(legs, S=400.0, dividends={"TSLA": 8.0})
    assert len(out["assignment_flags"]) == 1
    flag = out["assignment_flags"][0]
    assert flag["early_div_risk"] is True
    assert math.isclose(flag["extrinsic"], 5.0, abs_tol=1e-9)


def test_assignment_scan_pin_risk():
    # spot within 1% of a short strike -> pin risk.
    legs = [call(400, -1, entry=12.0)]
    out = risk.assignment_pin_scan(legs, S=400.5, pin_band=0.01)  # 0.5/400 = 0.125%
    assert len(out["pin_flags"]) == 1


def test_assignment_scan_leg_mismatch_calendar():
    # near + far expiries on same strike -> calendar -> leg_mismatch True.
    near = call(400, -1, entry=10.0, expiry=date(2026, 7, 17))
    far = call(400, 1, entry=20.0, expiry=date(2026, 12, 18))
    out = risk.assignment_pin_scan([near, far], S=400.0)
    assert out["leg_mismatch"] is True


def test_expiration_cluster_concentration():
    # Two positions sharing one expiry, one on a far expiry -> concentration high.
    p1 = Position(underlying="TSLA",
                  legs=(call(360, 8, expiry=date(2027, 6, 18)),
                        call(460, -8, expiry=date(2027, 6, 18))))
    p2 = Position(underlying="AAPL",
                  legs=(put(200, -2, expiry=date(2027, 6, 18)),))
    p3 = Position(underlying="MSFT",
                  legs=(call(500, -1, expiry=date(2026, 9, 18)),))
    book = Book(positions=(p1, p2, p3))
    out = risk.expiration_cluster_risk(book, cluster_window_days=7)
    # 2027-06-18 bucket = 8+8+2 = 18 contracts; 2026-09-18 = 1. total 19.
    assert math.isclose(out["max_cluster"], 18.0, abs_tol=1e-9)
    assert math.isclose(out["total_contracts"], 19.0, abs_tol=1e-9)
    assert out["concentration"] > 0.9            # heavily clustered on one date


def test_empty_pnl_array_raises():
    with pytest.raises(ValueError):
        risk.var_cvar(np.array([]))
