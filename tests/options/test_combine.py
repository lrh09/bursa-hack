"""Lock in the combination layer: selection, netting, liquidation, comparison.

Built on the full 9-position TSLA book (configs/options/tsla_book_2026-06-18.yaml),
loaded via bookio.load_book(REPO_ROOT/...). The book order (1-based ids) is:

  1 220C x10 long        2 380C x10 long          3 bull call 300/650 x5
  4 bull call 240/540 x2 5 bull call 400/500 x5   6 combo Jan27 x9
  7 combo Sep26 x2       8 short 460C x10          9 short 650C x3

Net-long book; positions 8/9 are net-short-call up-tails. Spot 389.80,
sigma 0.46, netliq 525898.

Maintenance routing (combine docstring, decisive ruling 1):
  - combine.combined_maintenance computes "all subset legs as ONE stress set"
    via risk.pm_stress_maintenance -> the netting-benefit number.
  - the comparison table's maintenance column uses margin.pm_maintenance
    (per-position sum) -> consistent with the book report.

Liquidation: EL(S')=NetLiq(S')-maintenance(S') swept over spot. The real book at
netliq=525898 is over-cushioned (the long book's downside loss caps well below
NLV), so a finite down-liquidation only solves once the cushion is thinned; the
test uses a reduced netliq where the breach genuinely exists, with the value/
maintenance routed through the exact contract primitives.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from bursahack.paths import REPO_ROOT
from bursahack.options import bookio, combine, risk
from bursahack.options.types import Book, Leg, Position, Right


BOOK_PATH = REPO_ROOT / "configs" / "options" / "tsla_book_2026-06-18.yaml"


@pytest.fixture(scope="module")
def book():
    return bookio.load_book(BOOK_PATH)


@pytest.fixture(scope="module")
def market(book):
    return book.markets["TSLA"]


@pytest.fixture(scope="module")
def account(book):
    return book.account


# ===========================================================================
# 1. select_subset validation + subset_to_legs flattening
# ===========================================================================
def test_select_subset_validation(book):
    n = len(book.positions)
    assert n == 9

    # out-of-range (high)
    with pytest.raises(ValueError):
        combine.select_subset(book, [1, n + 1])
    # out-of-range (zero / non-1-based)
    with pytest.raises(ValueError):
        combine.select_subset(book, [0])
    # duplicate id
    with pytest.raises(ValueError):
        combine.select_subset(book, [3, 3])
    # non-int id
    with pytest.raises(ValueError):
        combine.select_subset(book, ["3"])  # type: ignore[list-item]

    # valid -> Book of the right positions, in id order
    sub = combine.select_subset(book, [5, 1, 3])
    assert isinstance(sub, Book)
    assert [p.name for p in sub.positions] == [
        book.positions[4].name,
        book.positions[0].name,
        book.positions[2].name,
    ]
    assert sub.asof == book.asof

    # subset_to_legs flattens in order (5 has 2 legs, 1 has 1 leg, 3 has 2 legs)
    legs = combine.subset_to_legs(sub)
    assert len(legs) == 2 + 1 + 2
    assert all(isinstance(lg, Leg) for lg in legs)

    # subset_to_legs accepts a Position and a list[Leg] too
    pos = book.positions[0]
    assert combine.subset_to_legs(pos) == list(pos.legs)
    raw_legs = list(pos.legs)
    assert combine.subset_to_legs(raw_legs) == raw_legs


# ===========================================================================
# 2. combined maintenance of a hedged pair != sum (netting benefit > 0)
# ===========================================================================
def test_combined_maintenance_hedged_pair_ne_sum(market):
    # A canonical hedged pair routed through the EXACT contract primitives:
    # a long call + short call at the SAME strike. Individually each leg-set
    # has a real stress-decline maintenance; combined as ONE leg-set the up-tail
    # and down-tail offset, so combined maintenance collapses far below the sum.
    EXP = date(2027, 6, 18)
    long_c = Leg(right=Right.CALL, strike=390, expiry=EXP, qty=10, mult=100,
                 entry_price=0.0, underlying="TSLA")
    short_c = Leg(right=Right.CALL, strike=390, expiry=EXP, qty=-10, mult=100,
                  entry_price=0.0, underlying="TSLA")
    pos_long = Position(underlying="TSLA", legs=(long_c,), name="long 390C x10")
    pos_short = Position(underlying="TSLA", legs=(short_c,), name="short 390C x10")
    hedged = Book(positions=(pos_long, pos_short))

    cm = combine.combined_maintenance(hedged, [1, 2], market)
    # both per-position legs carry stress-decline maintenance
    assert cm["sum_per_position"] > 0.0
    # combined (one leg-set: long + short same strike) nets the worst-case decline
    assert cm["combined"] < cm["sum_per_position"]
    assert cm["netting_benefit"] > 0.0
    # cross-check the per-leg-set numbers are exactly risk.pm_stress_maintenance
    spot = market.spot
    sum_pp = (risk.pm_stress_maintenance([long_c], spot)["maintenance"]
              + risk.pm_stress_maintenance([short_c], spot)["maintenance"])
    combined = risk.pm_stress_maintenance([long_c, short_c], spot)["maintenance"]
    assert cm["sum_per_position"] == pytest.approx(sum_pp)
    assert cm["combined"] == pytest.approx(combined)


# ===========================================================================
# 3. liquidation point is finite on the down side for the net-long book
# ===========================================================================
def test_liquidation_point_finite_net_long(book, market, account):
    # The real book (netliq=525898) is over-cushioned on the down side: the
    # long-call book's worst-case downside loss caps ~407K, below NLV, so EL
    # never reaches 0 within span. Thin the cushion to a netliq where the down
    # breach genuinely exists, then verify the contract shape. NetLiq(S') and
    # maintenance(S') are routed through the exact contract primitives inside
    # combine.liquidation_point.
    acct = replace(account, netliq=400000.0)
    res = combine.liquidation_point(book, market, acct, direction="both")

    assert res["el_base"] > 0.0                      # solvent at base
    assert res["base_spot"] == pytest.approx(market.spot)

    down = res["down"]
    assert down is not None                          # a finite down liquidation solves
    assert 0.0 < down["spot"] < res["base_spot"]     # below base, positive spot
    assert down["pct"] < 0.0                         # a downward move
    # the solved EL is ~0 at the root (bisected to ~1e-3 of spot)
    assert abs(down["el_at"]) < 5_000.0

    # at the FULL netliq the down side does not breach within span (documented).
    full = combine.liquidation_point(book, market, account, direction="down")
    assert full["down"] is None
    assert "down side" in full["note"]


# ===========================================================================
# 4. compare_subsets shape: rows aligned to columns, EL/maintenance finite
# ===========================================================================
def test_compare_subsets_shape(book, market, account):
    import math

    cmp = combine.compare_subsets(book, market, account, [[1, 2, 3], [8, 9], [1, 5, 6, 8]])
    cols = cmp["columns"]
    assert "maintenance" in cols and "excess_liquidity" in cols
    assert "liquidation_down_pct" in cols and "liquidation_up_pct" in cols

    rows = cmp["subsets"]
    assert len(rows) == 3
    assert [r["label"] for r in rows] == ["1,2,3", "8,9", "1,5,6,8"]

    for r in rows:
        # every declared column key is present on the row
        for key in cols:
            assert key in r, f"row missing column {key}"
        assert math.isfinite(r["maintenance"])
        assert math.isfinite(r["excess_liquidity"])
        # EL == netliq - maintenance (per-position sum), consistent with the book
        assert r["excess_liquidity"] == pytest.approx(account.netliq - r["maintenance"])
        assert isinstance(r["defined_risk"], bool)
        # delta_equiv_shares is dollar_greeks['delta'] (already share-equivalent)
        assert r["delta_equiv_shares"] == pytest.approx(r["delta"])

    # the undefined-risk subset (8,9 = net short calls) is flagged undefined
    row_89 = next(r for r in rows if r["label"] == "8,9")
    assert row_89["defined_risk"] is False
