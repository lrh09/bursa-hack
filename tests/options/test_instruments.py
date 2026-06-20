"""Lock in the options value objects + named-strategy constructors.

Goldens are hand-computed and checked with math.isclose; the math is shown in
the comments (mirrors tests/test_costs.py). The two headline properties:

  * a vertical's maximum intrinsic spread value == the strike WIDTH, and
  * a long synthetic forward (long call + short put @ same K) has expiry payoff
    (S_T - K) for every S_T — the structural restatement of put-call parity.

Market state for any pricing-adjacent reference is MS_A (S=389.80, sigma=0.46,
T=1, r=0.045, q=0) but these tests are PURE structure: no Black-Scholes, only
intrinsic/sign/qty/mult bookkeeping, so they run with zero pricing deps.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from bursahack.options.instruments import (
    Account,
    AccountProfile,
    Book,
    D1D2,
    Greeks,
    Leg,
    MarketState,
    Measure,
    Position,
    Right,
    Style,
    bear_call_spread,
    bull_call_spread,
    butterfly,
    calendar,
    cash_secured_put,
    collar,
    covered_call,
    diagonal,
    from_legs,
    iron_butterfly,
    iron_condor,
    long_call,
    long_put,
    pmcc,
    ratio_spread,
    short_call,
    short_put,
    straddle,
    strangle,
    synthetic_long,
    synthetic_short,
    vertical,
)

EXP = date(2027, 6, 18)
NEAR = date(2026, 9, 18)


# =============================================================================
# Helpers
# =============================================================================
def position_payoff(pos: Position, S: float) -> float:
    """Gross signed intrinsic payoff (no net cost) at spot S, per position."""
    return sum(leg.signed_intrinsic_value(S) for leg in pos.legs)


def net_cost(pos: Position) -> float:
    """Signed net cash at fill (debit > 0 / credit < 0).

    Leg.signed_cashflow_entry already returns debit>0/credit<0 per leg, so the
    straight sum is the net entry cost.
    """
    return sum(leg.signed_cashflow_entry() for leg in pos.legs)


# =============================================================================
# Enums + value objects
# =============================================================================
def test_right_constructs_from_yaml_value():
    # YAML carries the VALUE; Right("C") must round-trip to Right.CALL.
    assert Right("C") is Right.CALL
    assert Right("P") is Right.PUT
    assert Right("S") is Right.STOCK
    assert Right("X") is Right.CASH


def test_style_and_measure_values():
    assert Style("european") is Style.EUROPEAN
    assert Measure("rn") is Measure.RISK_NEUTRAL
    assert Measure("rw") is Measure.REAL_WORLD


def test_leg_sign_and_cashflow_conventions():
    # Long 1 call @ entry 5.00, mult 100 -> you PAY 500 -> cashflow +500 (debit).
    long = Leg(Right.CALL, 360.0, EXP, qty=1, entry_price=5.0)
    assert long.is_long and long.sign == 1
    assert math.isclose(long.signed_cashflow_entry(), 500.0, rel_tol=1e-12)
    # Short 1 call @ entry 5.00 -> you RECEIVE 500 -> cashflow -500 (credit).
    short = Leg(Right.CALL, 360.0, EXP, qty=-1, entry_price=5.0)
    assert short.is_short and short.sign == -1
    assert math.isclose(short.signed_cashflow_entry(), -500.0, rel_tol=1e-12)


def test_leg_validation_rejects_bad_inputs():
    with pytest.raises(ValueError):
        Leg(Right.CALL, 360.0, EXP, qty=0)  # qty must be non-zero
    with pytest.raises(ValueError):
        Leg(Right.CALL, 360.0, EXP, qty=1, entry_price=-1.0)  # magnitude only
    with pytest.raises(ValueError):
        Leg(Right.CALL, 0.0, EXP, qty=1)  # option needs positive strike
    with pytest.raises(ValueError):
        Leg(Right.PUT, 100.0, EXP, qty=1, mult=0)  # positive multiplier


def test_leg_intrinsic_per_share():
    c = Leg(Right.CALL, 360.0, EXP, qty=1)
    p = Leg(Right.PUT, 360.0, EXP, qty=1)
    # call intrinsic at 390 = 30; put intrinsic at 390 = 0
    assert math.isclose(c.intrinsic(390.0), 30.0)
    assert math.isclose(p.intrinsic(390.0), 0.0)
    # at 330: call 0, put 30
    assert math.isclose(c.intrinsic(330.0), 0.0)
    assert math.isclose(p.intrinsic(330.0), 30.0)


def test_marketstate_rejects_q_and_divs_together():
    with pytest.raises(ValueError):
        MarketState(spot=100.0, q=0.02, div_schedule=((0.5, 1.0),))
    with pytest.raises(ValueError):
        MarketState(spot=-1.0)  # spot must be positive
    # both-zero / one-or-the-other is fine
    MarketState(spot=100.0, q=0.02)
    MarketState(spot=100.0, div_schedule=((0.5, 1.0),))


def test_account_profile_tax_gating():
    nra = AccountProfile.rh_default()
    assert nra.tax_status == "nra" and nra.residence == "MY"
    rules = nra.tax_rules()
    assert rules == {"wash_sale": False, "holding_split": False, "us_cgt": False}
    us = AccountProfile(tax_status="us_person")
    assert us.tax_rules() == {"wash_sale": True, "holding_split": True, "us_cgt": True}
    assert any("864(b)(2)" in n for n in nra.standing_notes())


def test_position_and_book_normalise_to_tuple():
    p = long_call("TSLA", 360, EXP)
    assert isinstance(p.legs, tuple)
    pos2 = Position("TSLA", [p.legs[0]])  # list -> tuple
    assert isinstance(pos2.legs, tuple)
    bk = Book([p])
    assert isinstance(bk.positions, tuple)
    with pytest.raises(ValueError):
        Position("TSLA", ())  # need at least one leg


def test_account_holds_single_netliq_source():
    acct = Account(profile=AccountProfile.rh_default(), cash=10_000.0, netliq=125_000.0)
    assert acct.netliq == 125_000.0 and acct.cash == 10_000.0


def test_greek_containers_are_data_only():
    g = Greeks(delta=0.5, gamma=0.01, theta_day=-0.1, theta_yr=-36.5, vega=0.2, rho=0.3)
    assert g.vanna is None and g.zomma is None  # unfilled higher orders default None
    d = D1D2(d1=0.1, d2=0.05, nd1=0.39, Nd1=0.54, Nd2=0.52)
    assert d.d1 == 0.1


# =============================================================================
# Single-leg constructors
# =============================================================================
def test_single_leg_constructors_set_signs():
    assert long_call("X", 100, EXP).legs[0].qty == 1
    assert long_put("X", 100, EXP).legs[0].qty == 1
    assert short_call("X", 100, EXP).legs[0].qty == -1
    assert short_put("X", 100, EXP).legs[0].qty == -1
    # right is correct on each
    assert long_call("X", 100, EXP).legs[0].right is Right.CALL
    assert long_put("X", 100, EXP).legs[0].right is Right.PUT
    # default multiplier is 100
    assert long_call("X", 100, EXP).legs[0].mult == 100


def test_single_leg_qty_magnitude_normalised():
    # passing a negative qty into a "long" builder still yields a long leg
    assert long_call("X", 100, EXP, qty=-3).legs[0].qty == 3
    assert short_call("X", 100, EXP, qty=3).legs[0].qty == -3


# =============================================================================
# Verticals — the WIDTH golden
# =============================================================================
def test_bull_call_spread_legs_and_golden_shape():
    # The TSLA worked example: long 360C, short 460C, x8, debit fills.
    bc = bull_call_spread("TSLA", 360, 460, EXP, qty=8, entry_long=91.96, entry_short=53.00)
    assert len(bc.legs) == 2
    lo, hi = bc.legs
    assert lo.right is Right.CALL and lo.strike == 360 and lo.qty == 8
    assert hi.right is Right.CALL and hi.strike == 460 and hi.qty == -8
    # net entry cash = debit per spread (91.96 - 53.00)=38.96/share x8 x100 = 31168
    # cashflow: long pays +91.96*8*100, short receives -53.00*8*100
    assert math.isclose(net_cost(bc), (91.96 - 53.00) * 8 * 100, rel_tol=1e-9)


def test_vertical_max_value_equals_width_bull_call():
    # PROPERTY: a one-wide vertical's max intrinsic spread value == strike width.
    # bull call 360/460: width=100. Deep ITM (S>=460): long 360C=100, short 460C=0
    # -> gross = (100 - 0) per share -> *mult*qty.
    bc = bull_call_spread("TSLA", 360, 460, EXP, qty=1)
    width = 460 - 360
    # at S=600 (well above both strikes): long 240, short -140 -> net 100 = width
    assert math.isclose(position_payoff(bc, 600.0) / 100.0, width, rel_tol=1e-12)
    # at S=460 exactly: long 100, short 0 -> 100 = width (the cap)
    assert math.isclose(position_payoff(bc, 460.0) / 100.0, width, rel_tol=1e-12)
    # below the long strike: 0
    assert math.isclose(position_payoff(bc, 300.0), 0.0)
    # the spread never exceeds width on the upside
    assert position_payoff(bc, 10_000.0) / 100.0 <= width + 1e-9


def test_vertical_max_value_equals_width_bear_put():
    # A bear put 460/360 (long 460P, short 360P) also caps at width=100.
    # Deep ITM downside (S=0): long 460P=460, short 360P=-360 -> net 100 = width.
    from bursahack.options.instruments import bear_put_spread

    bp = bear_put_spread("TSLA", 460, 360, EXP, qty=1)
    width = 460 - 360
    assert math.isclose(position_payoff(bp, 0.0) / 100.0, width, rel_tol=1e-12)
    assert position_payoff(bp, 1_000.0) == 0.0  # both puts worthless above


def test_bear_call_spread_is_credit_and_signs():
    bc = bear_call_spread("X", 500, 530, EXP, qty=1, entry_short=10.0, entry_long=2.0)
    short_leg, long_leg = bc.legs
    assert short_leg.strike == 500 and short_leg.qty == -1  # lower strike sold
    assert long_leg.strike == 530 and long_leg.qty == 1  # higher strike bought
    # net cash = -10 (received) + 2 (paid) per share -> credit < 0
    assert net_cost(bc) < 0
    assert math.isclose(net_cost(bc), (-10.0 + 2.0) * 100, rel_tol=1e-9)


def test_vertical_strike_order_guards():
    with pytest.raises(ValueError):
        bull_call_spread("X", 460, 360, EXP)  # K_long must be < K_short
    with pytest.raises(ValueError):
        bear_call_spread("X", 530, 500, EXP)  # K_short must be < K_long


# =============================================================================
# Synthetic — the PARITY golden
# =============================================================================
def test_synthetic_long_payoff_is_forward():
    # PROPERTY: long synthetic forward (long call + short put @K) pays (S_T - K)
    # for EVERY S_T. This is put-call parity restated at the payoff level.
    K = 400.0
    syn = synthetic_long("TSLA", K, EXP, qty=1)
    c, p = syn.legs
    assert c.right is Right.CALL and c.qty == 1
    assert p.right is Right.PUT and p.qty == -1
    for S in (1.0, 200.0, 400.0, 600.0, 1500.0):
        # per-share payoff = position payoff / mult
        per_share = position_payoff(syn, S) / 100.0
        assert math.isclose(per_share, S - K, abs_tol=1e-9), f"S={S}"


def test_synthetic_short_payoff_is_inverse_forward():
    K = 400.0
    syn = synthetic_short("TSLA", K, EXP, qty=1)
    for S in (1.0, 200.0, 400.0, 600.0):
        per_share = position_payoff(syn, S) / 100.0
        assert math.isclose(per_share, K - S, abs_tol=1e-9), f"S={S}"


def test_synthetic_long_plus_short_is_flat():
    # long synthetic + short synthetic @ same K nets to zero payoff everywhere.
    K = 400.0
    sl = synthetic_long("X", K, EXP)
    ss = synthetic_short("X", K, EXP)
    for S in (50.0, 400.0, 900.0):
        assert math.isclose(position_payoff(sl, S) + position_payoff(ss, S), 0.0, abs_tol=1e-9)


# =============================================================================
# Multi-leg constructors — leg counts, signs, ordering
# =============================================================================
def test_straddle_and_strangle_legs():
    st = straddle("X", 400, EXP, qty=2)
    assert {leg.right for leg in st.legs} == {Right.CALL, Right.PUT}
    assert all(leg.qty == 2 for leg in st.legs)  # both long
    short_st = straddle("X", 400, EXP, side="short")
    assert all(leg.qty == -1 for leg in short_st.legs)
    sg = strangle("X", 380, 420, EXP)
    put_leg, call_leg = sg.legs
    assert put_leg.right is Right.PUT and put_leg.strike == 380
    assert call_leg.right is Right.CALL and call_leg.strike == 420
    with pytest.raises(ValueError):
        strangle("X", 420, 380, EXP)  # put strike must be below call strike


def test_iron_condor_four_legs_and_signs():
    ic = iron_condor("X", 360, 380, 420, 440, EXP, qty=1)
    assert len(ic.legs) == 4
    pl, ps, cs, cl = ic.legs
    assert (pl.right, pl.qty) == (Right.PUT, 1)  # long put wing
    assert (ps.right, ps.qty) == (Right.PUT, -1)  # short put body
    assert (cs.right, cs.qty) == (Right.CALL, -1)  # short call body
    assert (cl.right, cl.qty) == (Right.CALL, 1)  # long call wing
    # net qty is zero (fully hedged structure)
    assert ic.net_qty == 0
    with pytest.raises(ValueError):
        iron_condor("X", 380, 360, 420, 440, EXP)  # strikes must ascend


def test_iron_butterfly_legs():
    ib = iron_butterfly("X", 360, 400, 440, EXP, qty=1)
    assert len(ib.legs) == 4
    # body has both a short put and short call at 400
    body = [leg for leg in ib.legs if leg.strike == 400]
    assert len(body) == 2 and all(leg.qty == -1 for leg in body)


def test_butterfly_ratio_is_1_2_1():
    fly = butterfly("X", 360, 400, 440, EXP, Right.CALL, qty=1)
    qtys = [leg.qty for leg in fly.legs]
    assert qtys == [1, -2, 1]  # +1 / -2 / +1
    # at the body strike, a long call fly peaks at (mid - low) = 40 per share
    assert math.isclose(position_payoff(fly, 400.0) / 100.0, 40.0, rel_tol=1e-12)
    # wings: payoff zero at/below low and at/above high
    assert math.isclose(position_payoff(fly, 360.0), 0.0)
    assert math.isclose(position_payoff(fly, 440.0), 0.0)


def test_calendar_and_diagonal_use_two_expiries():
    cal = calendar("X", 400, NEAR, EXP, Right.CALL, qty=1)
    near, far = cal.legs
    assert near.expiry == NEAR and near.qty == -1  # short near
    assert far.expiry == EXP and far.qty == 1  # long far
    assert near.strike == far.strike == 400  # same strike
    diag = diagonal("X", 400, NEAR, 420, EXP, Right.CALL, qty=1)
    dn, df = diag.legs
    assert dn.strike == 400 and df.strike == 420  # different strikes
    assert dn.expiry == NEAR and df.expiry == EXP


def test_collar_has_stock_and_protective_options():
    col = collar("X", shares=100, put_K=360, call_K=440, expiry=EXP)
    stock = [leg for leg in col.legs if leg.right is Right.STOCK][0]
    assert stock.qty == 100 and stock.mult == 1
    put = [leg for leg in col.legs if leg.right is Right.PUT][0]
    call = [leg for leg in col.legs if leg.right is Right.CALL][0]
    assert put.qty == 1 and call.qty == -1  # 100 shares -> 1 contract each
    with pytest.raises(ValueError):
        collar("X", 100, put_K=440, call_K=360, expiry=EXP)  # put_K < call_K


def test_covered_call_and_csp():
    cc = covered_call("X", shares=200, call_K=440, expiry=EXP)
    stock = [leg for leg in cc.legs if leg.right is Right.STOCK][0]
    short_c = [leg for leg in cc.legs if leg.right is Right.CALL][0]
    assert stock.qty == 200 and stock.mult == 1
    assert short_c.qty == -2  # 200 shares / 100 = 2 contracts short
    csp = cash_secured_put("X", 360, EXP, qty=3)
    assert csp.legs[0].right is Right.PUT and csp.legs[0].qty == -3


def test_pmcc_long_leap_short_near():
    p = pmcc("X", leap_K=300, leap_exp=EXP, short_K=440, short_exp=NEAR, qty=1)
    leap, near = p.legs
    assert leap.qty == 1 and leap.strike == 300 and leap.expiry == EXP
    assert near.qty == -1 and near.strike == 440 and near.expiry == NEAR


def test_ratio_spread_1x2_signs():
    r = ratio_spread("X", 400, 440, EXP, Right.CALL, long_qty=1, short_qty=2)
    lo, hi = r.legs
    assert lo.qty == 1 and hi.qty == -2  # 1x2 ratio -> unbounded tail downstream
    assert r.net_qty == -1


def test_from_legs_wraps_custom_structure():
    legs = [
        Leg(Right.CALL, 360, EXP, qty=1),
        Leg(Right.PUT, 360, EXP, qty=-1),
        Leg(Right.STOCK, 0.0, None, qty=-100, mult=1),
    ]
    pos = from_legs("X", legs)
    assert len(pos.legs) == 3 and pos.underlying == "X"


# =============================================================================
# x100 multiplier in the position-level value
# =============================================================================
def test_multiplier_scales_position_value():
    # one long 360C at S=390 -> per-share intrinsic 30 -> position value 30*100*1 = 3000
    lc = long_call("X", 360, EXP, qty=1)
    assert math.isclose(position_payoff(lc, 390.0), 3000.0, rel_tol=1e-12)
    # x8 contracts -> 24000
    lc8 = long_call("X", 360, EXP, qty=8)
    assert math.isclose(position_payoff(lc8, 390.0), 24000.0, rel_tol=1e-12)
