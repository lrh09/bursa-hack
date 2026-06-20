"""Lock strategy intelligence against hand-computed goldens.

Market states (pinned by the build contract):
  MS_A: S=389.80, sigma=0.46, T=1, r=0.045, q=0   (TSLA bull-call economics)
  MS_B: S=396.40, T=1 (sigma per row), r=0.045, q=0

Goldens this file locks (contract sec 7):
  - strike_for_delta inversion: target 0.487 -> ~460, target 0.692 -> ~360 (MS_A)
  - over-long book -> recommends CALL credit spread, REJECTS put credit (w/ reason)
  - roll cash/delta: a capped near-expiry spread has ~0 forward delta -> a roll is a
    fresh position (added delta ~ new structure's delta; net cash math)
  - classifier on known leg sets (bull/bear call, IC, straddle, covered call, PMCC,
    calendar)
  - PMCC gates: deep-ITM long (delta>=0.75), no-loss safety, ~0 incremental BP
  - candidate ranking: defined-risk beats undefined-tail (L7 hard gate)

If the pricing core (bsm/core/types) hasn't landed yet (parallel build), the whole
module skips rather than erroring at collection -- it goes green the moment the
critical-path modules exist.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import pytest

# The pricing core is built by sibling agents. Skip cleanly until it lands so this
# file never breaks collection of the wider suite.
pytest.importorskip("bursahack.options.bsm")
pytest.importorskip("bursahack.options.core")

from bursahack.options.types import Book, Leg, MarketState, Position, Right  # noqa: E402
from bursahack.options import strategy  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: the two pinned market states + canonical legs
# ---------------------------------------------------------------------------
ASOF = datetime(2026, 6, 18, 16, 0, 0)
EXP_1Y = date(2027, 6, 18)        # ~1 year from asof (365 days)


def ms_a() -> MarketState:
    # S=389.80, sigma=0.46, r=0.045, q=0
    return MarketState(spot=389.80, r=0.045, q=0.0, sigma=0.46, asof=ASOF)


def ms_b() -> MarketState:
    # S=396.40, r=0.045, q=0
    return MarketState(spot=396.40, r=0.045, q=0.0, sigma=0.46, asof=ASOF)


def bull_call_single() -> list[Leg]:
    # TSLA bull call 360/460, 1 contract. delta(360)=0.692, delta(460)=0.487.
    return [
        Leg(Right.CALL, 360.0, EXP_1Y, qty=1, mult=100, entry_price=91.96, underlying="TSLA"),
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-1, mult=100, entry_price=53.00, underlying="TSLA"),
    ]


# ===========================================================================
# strike_for_delta -- delta -> strike inversion (FROZEN golden, MS_A)
# ===========================================================================
def test_strike_for_delta_call_targets():
    # MS_A: call delta is decreasing in K. The 460 call is a 0.487-delta wing,
    # the 360 call is a 0.692-delta wing -> inversion must return those strikes.
    m = ms_a()
    k_487 = strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 0.487, Right.CALL)
    k_692 = strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 0.692, Right.CALL)
    assert math.isclose(k_487, 460.0, abs_tol=1.0)   # ~460.09
    assert math.isclose(k_692, 360.0, abs_tol=1.0)   # ~359.87


def test_strike_for_delta_monotone_call():
    # A smaller target delta (more OTM) must map to a HIGHER strike.
    m = ms_a()
    k_30 = strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 0.30, Right.CALL)
    k_50 = strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 0.50, Right.CALL)
    assert k_30 > k_50


def test_strike_for_delta_put_side():
    # Put inversion takes |target|; result is a positive strike, more-OTM put = lower K.
    m = ms_a()
    k_25 = strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 0.25, Right.PUT)
    k_40 = strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 0.40, Right.PUT)
    assert k_25 > 0 and k_40 > 0
    assert k_25 < k_40                                # 25d put strike below 40d put strike


def test_strike_for_delta_rejects_bad_inputs():
    m = ms_a()
    with pytest.raises(ValueError):
        strategy.strike_for_delta(m.spot, 0.0, m.r, m.q, m.sigma, 0.30, Right.CALL)
    with pytest.raises(ValueError):
        strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, 0.0, 0.30, Right.CALL)
    with pytest.raises(ValueError):
        strategy.strike_for_delta(m.spot, 1.0, m.r, m.q, m.sigma, 1.5, Right.CALL)


# ===========================================================================
# net_delta_shares -- the building block for posture
# ===========================================================================
def test_single_bull_call_net_delta_shares():
    # Per-share net delta = 0.692 - 0.487 = 0.205. x100 mult x1 contract = 20.5 share-deltas.
    nd = strategy.net_delta_shares(bull_call_single(), 389.80, ms_a())
    assert math.isclose(nd, 20.5, abs_tol=0.7)       # ~20.45


def test_eight_lot_bull_call_net_delta_shares():
    # x8 contracts -> 0.205 * 100 * 8 = 164 share-deltas.
    legs = [
        Leg(Right.CALL, 360.0, EXP_1Y, qty=8, mult=100, entry_price=91.96, underlying="TSLA"),
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-8, mult=100, entry_price=53.00, underlying="TSLA"),
    ]
    nd = strategy.net_delta_shares(legs, 389.80, ms_a())
    assert math.isclose(nd, 164.0, abs_tol=6.0)      # ~163.6


def test_stock_leg_delta_is_qty():
    # A stock leg contributes exactly its (signed) share count: delta of underlying = 1.
    legs = [Leg(Right.STOCK, 0.0, None, qty=300, mult=1, underlying="TSLA")]
    assert math.isclose(strategy.net_delta_shares(legs, 389.80, ms_a()), 300.0, abs_tol=1e-9)


# ===========================================================================
# Income posture -- over-long book -> CALL credit; REJECT put credit (FROZEN)
# ===========================================================================
def test_over_long_book_recommends_call_credit_rejects_put_credit():
    # A heavily long-delta book (20 long ITM calls) sits well above the neutral band.
    m = ms_a()
    book_legs = [Leg(Right.CALL, 300.0, EXP_1Y, qty=20, mult=100, underlying="TSLA")]
    adv = strategy.income_posture_advice(book_legs, m, netliq=100_000.0)
    assert adv.posture == "over_long"
    assert adv.net_delta_shares > 0
    # recommend CALL credit (adds negative delta, trims the book)
    assert adv.recommended_side == "call_credit"
    # REJECT put credit, with a reason that names the correlated long-delta danger
    assert adv.anti_recommendation == "put_credit"
    assert adv.anti_reason is not None
    assert "long" in adv.anti_reason.lower()


def test_over_short_book_is_symmetric():
    # Over-short -> recommend PUT credit (adds positive delta), reject CALL credit.
    m = ms_a()
    book_legs = [Leg(Right.CALL, 300.0, EXP_1Y, qty=-20, mult=100, underlying="TSLA")]
    adv = strategy.income_posture_advice(book_legs, m, netliq=100_000.0)
    assert adv.posture == "over_short"
    assert adv.recommended_side == "put_credit"
    assert adv.anti_recommendation == "call_credit"


def test_balanced_book_no_anti_recommendation():
    # A tiny directional tilt vs a huge NLV (wide band) -> balanced, no anti-rec.
    m = ms_a()
    legs = [
        Leg(Right.CALL, 460.0, EXP_1Y, qty=1, mult=100, underlying="TSLA"),
        Leg(Right.PUT, 320.0, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
    ]
    adv = strategy.income_posture_advice(legs, m, netliq=50_000_000.0)
    assert adv.posture == "balanced"
    assert adv.recommended_side is None
    assert adv.anti_recommendation is None


def test_posture_respects_explicit_band():
    # With a tight explicit band, even a modest long tilt reads as over-long.
    m = ms_a()
    legs = [Leg(Right.CALL, 460.0, EXP_1Y, qty=1, mult=100, underlying="TSLA")]
    adv = strategy.income_posture_advice(legs, m, netliq=1.0,
                                         target_band_shares=(-1.0, 1.0))
    assert adv.posture == "over_long"
    assert adv.recommended_side == "call_credit"


# ===========================================================================
# Roll analysis -- capped near-expiry spread has ~0 forward delta (FROZEN)
# ===========================================================================
def test_roll_capped_near_expiry_has_zero_forward_delta():
    # OLD: a deep-ITM bull call 300/320 expiring in 4 days -> capped & pinned.
    # Its forward delta is ~0 (value is locked), so a roll is a FRESH position:
    # added_delta == the NEW structure's delta alone (not new - live_old).
    m = ms_a()
    near = date(2026, 6, 22)        # 4 days from asof
    old = [
        Leg(Right.CALL, 300.0, near, qty=1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 320.0, near, qty=-1, mult=100, underlying="TSLA"),
    ]
    new = bull_call_single()
    roll = strategy.analyze_roll(old, new, m)
    assert roll["old_is_capped"] is True
    assert roll["old_is_pinned"] is True
    assert roll["old_forward_delta_shares"] == 0.0
    # added delta equals the new structure's delta (forward old delta is 0)
    assert math.isclose(roll["added_delta_shares"], roll["new_delta_shares"], abs_tol=1e-9)
    # new bull-call single carries ~+20.5 share deltas
    assert math.isclose(roll["new_delta_shares"], 20.5, abs_tol=0.7)


def test_roll_net_cash_sign():
    # Rolling from a low-mark old spread into a higher-mark new spread is a net DEBIT
    # (net_cash > 0): you pay to put on more premium/structure.
    m = ms_a()
    near = date(2026, 6, 22)
    old = [
        Leg(Right.CALL, 300.0, near, qty=1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 320.0, near, qty=-1, mult=100, underlying="TSLA"),
    ]
    new = bull_call_single()
    roll = strategy.analyze_roll(old, new, m)
    assert roll["net_cash"] > 0      # net debit paid to roll into the wider/farther spread


def test_roll_far_dated_old_keeps_live_forward_delta():
    # A far-dated (NOT pinned) old spread keeps its live delta as forward delta,
    # so the roll's added delta is the genuine (new - old) difference.
    m = ms_a()
    old = bull_call_single()                       # 1y out -> not pinned
    new = bull_call_single()                       # roll into the same -> added ~0
    roll = strategy.analyze_roll(old, new, m)
    assert roll["old_is_pinned"] is False
    assert roll["old_forward_delta_shares"] != 0.0
    assert math.isclose(roll["added_delta_shares"], 0.0, abs_tol=1e-9)


def test_roll_flags_cap_stacking():
    # If the new short lands on a strike where the book already holds a short of the
    # same (symbol,right,expiry), the roll flags cap-ladder stacking.
    m = ms_a()
    near = date(2026, 6, 22)
    old = [
        Leg(Right.CALL, 300.0, near, qty=1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 320.0, near, qty=-1, mult=100, underlying="TSLA"),
    ]
    new = bull_call_single()                       # short 460C @ EXP_1Y
    existing = Position("TSLA", (
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-3, mult=100, underlying="TSLA"),
    ))
    book = Book(positions=(existing,))
    roll = strategy.analyze_roll(old, new, m, book=book)
    assert len(roll["cap_stacking"]) == 1
    assert roll["cap_stacking"][0]["strike"] == 460.0


# ===========================================================================
# Classifier -- known leg sets round-trip to the right name (fallback path)
# ===========================================================================
def test_classify_bull_call_spread():
    lbl = strategy._classify_fallback(bull_call_single())
    assert lbl.name == "bull_call_spread"


def test_classify_bear_call_spread():
    # short lower / long higher = credit call vertical (the 500/530 shape)
    legs = [
        Leg(Right.CALL, 500.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
        Leg(Right.CALL, 530.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(legs).name == "bear_call_spread"


def test_classify_bull_put_spread():
    # short higher / long lower = credit put vertical
    legs = [
        Leg(Right.PUT, 360.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
        Leg(Right.PUT, 340.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(legs).name == "bull_put_spread"


def test_classify_straddle_and_strangle():
    straddle = [
        Leg(Right.CALL, 390.0, EXP_1Y, qty=1, mult=100, underlying="X"),
        Leg(Right.PUT, 390.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(straddle).name == "straddle"
    strangle = [
        Leg(Right.CALL, 420.0, EXP_1Y, qty=1, mult=100, underlying="X"),
        Leg(Right.PUT, 360.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(strangle).name == "strangle"


def test_classify_iron_condor():
    legs = [
        Leg(Right.PUT, 300.0, EXP_1Y, qty=1, mult=100, underlying="X"),
        Leg(Right.PUT, 320.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
        Leg(Right.CALL, 480.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(legs).name == "iron_condor"


def test_classify_iron_butterfly():
    legs = [
        Leg(Right.PUT, 380.0, EXP_1Y, qty=1, mult=100, underlying="X"),
        Leg(Right.PUT, 400.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
        Leg(Right.CALL, 400.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
        Leg(Right.CALL, 420.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(legs).name == "iron_butterfly"


def test_classify_4leg_synthetic_is_risk_reversal_not_iron_condor():
    # +470C / −470P (synthetic LONG forward @470) + short 600C wing + long 380P
    # floor. This is a DIRECTIONAL bullish risk-reversal (net +delta), NOT a
    # delta-neutral iron condor — the same-strike synthetic body must win over the
    # loose 4-leg iron rule. (Regression: the classifier used to call this
    # iron_condor/credit, which mislabeled a long-delta combo as neutral/short-vol.)
    legs = [
        Leg(Right.CALL, 470.0, EXP_1Y, qty=9, mult=100, underlying="X"),
        Leg(Right.PUT, 470.0, EXP_1Y, qty=-9, mult=100, underlying="X"),
        Leg(Right.CALL, 600.0, EXP_1Y, qty=-9, mult=100, underlying="X"),
        Leg(Right.PUT, 380.0, EXP_1Y, qty=9, mult=100, underlying="X"),
    ]
    cls = strategy._classify_fallback(legs)
    assert cls.name == "risk_reversal"
    assert cls.variant == "long"
    assert cls.name != "iron_condor"


def test_classify_4leg_short_synthetic_is_risk_reversal_short():
    # −360C / +360P (synthetic SHORT @360) + long 500C + short 300P -> bearish RR.
    legs = [
        Leg(Right.CALL, 360.0, EXP_1Y, qty=-2, mult=100, underlying="X"),
        Leg(Right.PUT, 360.0, EXP_1Y, qty=2, mult=100, underlying="X"),
        Leg(Right.CALL, 500.0, EXP_1Y, qty=2, mult=100, underlying="X"),
        Leg(Right.PUT, 300.0, EXP_1Y, qty=-2, mult=100, underlying="X"),
    ]
    cls = strategy._classify_fallback(legs)
    assert cls.name == "risk_reversal"
    assert cls.variant == "short"


def test_classify_butterfly():
    legs = [
        Leg(Right.CALL, 360.0, EXP_1Y, qty=1, mult=100, underlying="X"),
        Leg(Right.CALL, 390.0, EXP_1Y, qty=-2, mult=100, underlying="X"),
        Leg(Right.CALL, 420.0, EXP_1Y, qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(legs).name == "butterfly"


def test_classify_covered_call_and_csp():
    cc = [
        Leg(Right.STOCK, 0.0, None, qty=100, mult=1, underlying="X"),
        Leg(Right.CALL, 420.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(cc).name == "covered_call"
    csp = [Leg(Right.PUT, 360.0, EXP_1Y, qty=-1, mult=100, underlying="X")]
    assert strategy._classify_fallback(csp).name == "cash_secured_put"


def test_classify_collar():
    legs = [
        Leg(Right.STOCK, 0.0, None, qty=100, mult=1, underlying="X"),
        Leg(Right.PUT, 360.0, EXP_1Y, qty=1, mult=100, underlying="X"),
        Leg(Right.CALL, 440.0, EXP_1Y, qty=-1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(legs).name == "collar"


def test_classify_pmcc_and_calendar():
    # PMCC: long deep LEAP (lower strike, farther expiry) + short near OTM call.
    pmcc = [
        Leg(Right.CALL, 300.0, date(2028, 6, 18), qty=1, mult=100, underlying="X"),
        Leg(Right.CALL, 440.0, date(2026, 7, 18), qty=-1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(pmcc).name == "pmcc"
    # Calendar: same strike, different expiries.
    cal = [
        Leg(Right.CALL, 400.0, date(2026, 7, 18), qty=-1, mult=100, underlying="X"),
        Leg(Right.CALL, 400.0, date(2026, 12, 18), qty=1, mult=100, underlying="X"),
    ]
    assert strategy._classify_fallback(cal).name == "calendar"


def test_classify_single_options():
    assert strategy._classify_fallback(
        [Leg(Right.CALL, 400.0, EXP_1Y, qty=1, mult=100)]).name == "long_call"
    assert strategy._classify_fallback(
        [Leg(Right.PUT, 360.0, EXP_1Y, qty=1, mult=100)]).name == "long_put"
    assert strategy._classify_fallback(
        [Leg(Right.CALL, 400.0, EXP_1Y, qty=-1, mult=100)]).name == "short_call"


# ===========================================================================
# PMCC analysis -- gates (FROZEN): deep-ITM long, no-loss safety, ~0 incr BP
# ===========================================================================
def test_pmcc_valid_deep_itm():
    # Long 300C (deep ITM, delta>=0.75 at S~396) + short 440C. net_debit = 120-8 = 112.
    # short strike 440 >= long 300 + 112 = 412 -> no-loss safe -> incremental BP ~ 0.
    m = ms_b()
    leap = Leg(Right.CALL, 300.0, date(2028, 6, 18), qty=1, mult=100,
               entry_price=120.0, underlying="TSLA")
    short = Leg(Right.CALL, 440.0, date(2026, 7, 18), qty=-1, mult=100,
                entry_price=8.0, underlying="TSLA")
    res = strategy.analyze_pmcc(leap, short, m)
    assert res["long_delta"] >= 0.75
    assert res["deep_itm"] is True
    assert math.isclose(res["net_debit"], 112.0, abs_tol=1e-9)
    assert res["no_loss_safe"] is True
    assert res["bp_incremental"] == 0.0          # covered short adds ~0 BP
    assert res["ok"] is True
    # max profit if called = (440-300) - 112 = 28/share * 100 = 2800
    assert math.isclose(res["max_profit_if_called"], 2800.0, abs_tol=1e-9)


def test_pmcc_rejects_shallow_long():
    # A 440-strike "leap" at S~396 is NOT deep ITM (delta < 0.75) -> rejected.
    m = ms_b()
    leap = Leg(Right.CALL, 440.0, date(2028, 6, 18), qty=1, mult=100,
               entry_price=60.0, underlying="TSLA")
    short = Leg(Right.CALL, 440.0, date(2026, 7, 18), qty=-1, mult=100,
                entry_price=8.0, underlying="TSLA")
    res = strategy.analyze_pmcc(leap, short, m)
    assert res["deep_itm"] is False
    assert res["ok"] is False
    assert any("0.75" in r for r in res["reasons"])


def test_pmcc_rejects_unsafe_short_strike():
    # Deep-ITM long but short strike too low to cover the net debit -> not no-loss safe.
    m = ms_b()
    leap = Leg(Right.CALL, 300.0, date(2028, 6, 18), qty=1, mult=100,
               entry_price=120.0, underlying="TSLA")
    short = Leg(Right.CALL, 390.0, date(2026, 7, 18), qty=-1, mult=100,
                entry_price=20.0, underlying="TSLA")
    # net_debit = 120-20 = 100; short 390 < 300+100 = 400 -> unsafe
    res = strategy.analyze_pmcc(leap, short, m)
    assert res["no_loss_safe"] is False
    assert res["ok"] is False
    assert res["bp_incremental"] > 0.0           # the uncovered gap shows as BP


# ===========================================================================
# select_strikes -- empty-shelf, target-delta candidates
# ===========================================================================
def test_select_strikes_defined_risk_call_credit():
    m = ms_a()
    cands = strategy.select_strikes(
        {"symbol": "TSLA", "side": "call", "target_delta": 0.25, "width": 30,
         "expiry": EXP_1Y, "n": 3},
        m, Book(positions=()))
    assert len(cands) == 3
    for c in cands:
        assert c.long_strike > c.short_strike            # call credit: long wing higher
        assert math.isclose(c.long_strike - c.short_strike, 30.0, abs_tol=1.0)
        assert c.est_credit > 0                          # we collect premium
        assert c.max_loss > 0                            # defined risk
        assert 0.0 <= c.est_pop <= 1.0
        assert c.shelf_clear is True                     # empty book -> all clear


def test_select_strikes_flags_occupied_shelf():
    # If the book already shorts a strike a candidate lands on, shelf_clear is False.
    m = ms_a()
    cands = strategy.select_strikes(
        {"symbol": "TSLA", "side": "call", "target_delta": 0.25, "width": 30,
         "expiry": EXP_1Y, "n": 3},
        m, Book(positions=()))
    occupied_strike = cands[0].short_strike
    book = Book(positions=(Position("TSLA", (
        Leg(Right.CALL, occupied_strike, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
    )),))
    cands2 = strategy.select_strikes(
        {"symbol": "TSLA", "side": "call", "target_delta": 0.25, "width": 30,
         "expiry": EXP_1Y, "n": 3},
        m, book)
    hit = [c for c in cands2 if c.short_strike == occupied_strike]
    assert hit and hit[0].shelf_clear is False


def test_select_strikes_requires_expiry():
    with pytest.raises(ValueError):
        strategy.select_strikes({"symbol": "TSLA", "side": "call"}, ms_a(),
                                Book(positions=()))


# ===========================================================================
# rank_candidates -- defined-risk gate (L7) sinks undefined tails
# ===========================================================================
def test_rank_defined_risk_beats_naked_short():
    m = ms_a()
    spread = Position("TSLA", (
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 490.0, EXP_1Y, qty=1, mult=100, underlying="TSLA"),
    ))
    naked = Position("TSLA", (
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
    ))
    ranked = strategy.rank_candidates([naked, spread], m, Book(positions=()))
    # the defined-risk spread ranks first; the naked short is gated to the bottom
    assert ranked[0]["defined_risk"] is True
    assert ranked[0]["gates_passed"] is True
    assert ranked[-1]["gates_passed"] is False
    assert any("undefined" in r.lower() for r in ranked[-1]["gate_reasons"])


def test_rank_returns_one_row_per_candidate():
    m = ms_a()
    spread = Position("TSLA", (
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 490.0, EXP_1Y, qty=1, mult=100, underlying="TSLA"),
    ))
    iron_condor = Position("TSLA", (
        Leg(Right.PUT, 320.0, EXP_1Y, qty=1, mult=100, underlying="TSLA"),
        Leg(Right.PUT, 340.0, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 460.0, EXP_1Y, qty=-1, mult=100, underlying="TSLA"),
        Leg(Right.CALL, 480.0, EXP_1Y, qty=1, mult=100, underlying="TSLA"),
    ))
    ranked = strategy.rank_candidates([spread, iron_condor], m, Book(positions=()))
    assert len(ranked) == 2
    for row in ranked:
        assert "score" in row and "name" in row and "defined_risk" in row
