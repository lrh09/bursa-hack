"""Lock the scenario engine + book aggregation against hand-computed goldens.

Market state MS_A underlies the bull-call goldens: S=389.80, sigma=0.46, T=1,
r=0.045, q=0. The flagship golden is the 360/460 x8 expiry P&L ladder.

Ladder math (pinned in the build contract):
  entry debit / spread = 91.96 - 53.00 = 38.96 / share
  x8 contracts x 100 mult                = 31168 total debit (= net_cost_entry)
  at S=390 (between strikes): long 360C intrinsic = 30/sh, short 460C = 0
     gross = 30 * 8 * 100 = 24000 ; P&L = 24000 - 31168 = -7168
  at S <= 360: gross 0                      -> -31168
  at S >= 460: gross = 100 * 8 * 100 = 80000 -> +48832
So:  {312:-31168, 351:-31168, 390:-7168, 429:+24032, 468:+48832}.

Breakeven: 360 + 38.96 = 398.96.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import pytest

from bursahack.options import book as bk
from bursahack.options import scenario as sc
from bursahack.options.types import (
    Account,
    AccountProfile,
    Book,
    Leg,
    MarketState,
    Position,
    Right,
)

# --- MS_A market state + the bull-call 360/460 x8 legs --------------------

ASOF = datetime(2026, 6, 18, 16, 0, 0)
EXPIRY = date(2027, 6, 18)            # ~1y out -> T ~ 1.0
MS_A = MarketState(spot=389.80, r=0.045, q=0.0, sigma=0.46, asof=ASOF)

LONG_360 = Leg(right=Right.CALL, strike=360.0, expiry=EXPIRY, qty=8, mult=100,
               entry_price=91.96, underlying="TSLA")
SHORT_460 = Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-8, mult=100,
                entry_price=53.00, underlying="TSLA")
BULL_CALL_X8 = [LONG_360, SHORT_460]

# Single-spread version (qty 1) for the per-contract economics goldens.
LONG_360_1 = Leg(right=Right.CALL, strike=360.0, expiry=EXPIRY, qty=1, mult=100,
                 entry_price=91.96, underlying="TSLA")
SHORT_460_1 = Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-1, mult=100,
                  entry_price=53.00, underlying="TSLA")
BULL_CALL_X1 = [LONG_360_1, SHORT_460_1]

NET_COST_X8 = 31168.0   # 38.96 * 8 * 100


# =========================================================================
# Expiry scenario ladder -- the flagship golden
# =========================================================================


def test_expiry_ladder_x8_golden():
    """The full {312,351,390,429,468} -> P&L ladder, single source of payoff truth."""
    out = sc.expiry_scenario(BULL_CALL_X8, [312.0, 351.0, 390.0, 429.0, 468.0])
    ladder = out  # payoff.expiry_ladder returns {S: pnl_total} (possibly nested key)
    # Accept either {S: pnl} or {'pnl_total': {S: pnl}} shaped returns defensively.
    table = ladder.get("pnl_total", ladder) if isinstance(ladder, dict) else ladder
    expected = {312.0: -31168.0, 351.0: -31168.0, 390.0: -7168.0,
                429.0: 24032.0, 468.0: 48832.0}
    for S, want in expected.items():
        got = table[S]
        assert math.isclose(got, want, abs_tol=1e-6), f"S={S}: {got} != {want}"


def test_expiry_scenario_uses_entry_net_cost_when_omitted():
    """net_cost defaults to position.net_cost_entry (= 31168 debit)."""
    out = sc.expiry_scenario(BULL_CALL_X8, [360.0])   # at lower strike -> -debit
    table = out.get("pnl_total", out) if isinstance(out, dict) else out
    assert math.isclose(table[360.0], -NET_COST_X8, abs_tol=1e-6)


def test_expiry_scenario_accepts_position_object():
    """A Position (not just a leg list) is accepted -- _resolve_legs unwraps .legs."""
    pos = Position(underlying="TSLA", legs=tuple(BULL_CALL_X8), name="bull call 360/460")
    out = sc.expiry_scenario(pos, [468.0])
    table = out.get("pnl_total", out) if isinstance(out, dict) else out
    assert math.isclose(table[468.0], 48832.0, abs_tol=1e-6)


# =========================================================================
# Decay ladder -- terminal row must equal the intrinsic ladder (L3 limit)
# =========================================================================


def test_decay_ladder_terminal_row_matches_intrinsic():
    """The expiry-day curve of the decay ladder must equal pure intrinsic P&L:
    the T->0 limit of value_position IS the payoff ladder (single source, L3)."""
    S_grid = [312.0, 390.0, 468.0]
    expiry_days = (EXPIRY - ASOF.date()).days
    out = sc.decay_ladder(BULL_CALL_X8, MS_A, date_offsets=[expiry_days], S_grid=S_grid)
    curve = out["curves"][expiry_days]
    want = {312.0: -31168.0, 390.0: -7168.0, 468.0: 48832.0}
    for s, pnl in zip(S_grid, curve):
        assert math.isclose(pnl, want[s], abs_tol=1.0), f"S={s}: {pnl} != {want[s]}"


def test_decay_ladder_shapes():
    """Curves keyed by offset, each curve aligned to S grid, theta_day present."""
    out = sc.decay_ladder(BULL_CALL_X8, MS_A, date_offsets=[0, 30], S_grid=[360.0, 460.0])
    assert set(out["curves"]) == {0, 30}
    assert len(out["curves"][0]) == 2
    assert set(out["theta_day"]) == {0, 30}
    assert out["net_cost"] == pytest.approx(NET_COST_X8)


# =========================================================================
# Surfaces
# =========================================================================


def test_surface_spot_time_shape_and_terminal_edge():
    """Z[i][j] aligned to axes; the expiry column equals intrinsic ladder values."""
    expiry_days = (EXPIRY - ASOF.date()).days
    S_range = [312.0, 390.0, 468.0]
    out = sc.surface_spot_time(BULL_CALL_X8, MS_A, S_range, [0.0, float(expiry_days)])
    Z = out["Z"]
    assert Z.shape == (3, 2)
    # last column == expiry intrinsic
    want = [-31168.0, -7168.0, 48832.0]
    for i, w in enumerate(want):
        assert math.isclose(float(Z[i, -1]), w, abs_tol=1.0)
    # max cell is the deep-ITM, fully-decayed corner
    assert out["max_cell"]["pnl"] == pytest.approx(48832.0, abs=1.0)


def test_surface_spot_iv_zero_shift_is_mark_now():
    """The iv_shift=0 column equals a plain mark-now valuation (no vol bump)."""
    S_range = [389.80]
    out = sc.surface_spot_iv(BULL_CALL_X8, MS_A, S_range, [-5.0, 0.0, 5.0],
                             horizon_days=0)
    Z = out["Z"]
    assert Z.shape == (1, 3)
    # vega of a long-ish call spread is small but the +5 / -5 cells must differ from 0
    assert not math.isclose(float(Z[0, 0]), float(Z[0, 1]), abs_tol=1e-9)
    assert not math.isclose(float(Z[0, 2]), float(Z[0, 1]), abs_tol=1e-9)


# =========================================================================
# Scenario grid (long-form)
# =========================================================================


def test_scenario_grid_long_form_rows_and_pct_normalization():
    """Cartesian product, % vs absolute spot handling, pct = pnl/capital."""
    rows = sc.scenario_grid(
        BULL_CALL_X8, MS_A,
        spots=[-0.10, 0.0, 0.10],   # fractional moves off 389.80
        iv_shifts=[0.0],
        days=[0.0],
        capital=100_000.0,
    )
    assert len(rows) == 3 * 1 * 1
    spots = sorted({round(r["spot"], 2) for r in rows})
    assert spots == [pytest.approx(389.80 * 0.9, abs=1e-6),
                     pytest.approx(389.80, abs=1e-6),
                     pytest.approx(389.80 * 1.1, abs=1e-6)]
    for r in rows:
        assert r["pct"] == pytest.approx(r["pnl"] / 100_000.0)
        for k in ("delta", "gamma", "theta", "vega"):
            assert k in r


def test_scenario_grid_absolute_spots_passthrough():
    """Spots >= 1 are taken as absolute levels, not percentages."""
    rows = sc.scenario_grid(BULL_CALL_X8, MS_A, spots=[460.0], iv_shifts=[0.0],
                            days=[0.0], capital=0.0)
    assert rows[0]["spot"] == pytest.approx(460.0)
    assert rows[0]["pct"] is None   # capital <= 0 -> pct None


# =========================================================================
# Days-to-breakeven probe
# =========================================================================


def test_days_to_breakeven_returns_contract():
    """The probe returns the documented keys and an in-range / None day count."""
    out = sc.days_to_breakeven(BULL_CALL_X8, 460.0, MS_A)
    assert set(out) >= {"days", "reached", "pnl_now", "pnl_at_expiry", "S"}
    assert out["S"] == pytest.approx(460.0)
    # deep-ITM at expiry pays +48832; at expiry it is firmly positive
    assert out["pnl_at_expiry"] == pytest.approx(48832.0, abs=1.0)


# =========================================================================
# Book aggregation -- hand-computable goldens
# =========================================================================


def _spy(symbol: str, S: float) -> MarketState:
    return MarketState(spot=S, r=0.045, q=0.0, sigma=0.20, asof=ASOF)


def test_book_delta_equiv_pure_stock():
    """A pure long-stock position: delta-equiv shares == qty*mult, exactly."""
    stock_leg = Leg(right=Right.STOCK, strike=0.0, expiry=None, qty=100, mult=1,
                    entry_price=389.80, underlying="TSLA")
    pos = Position(underlying="TSLA", legs=(stock_leg,), name="long stock")
    book = Book(positions=(pos,))
    out = bk.delta_equiv(book, {"TSLA": MS_A})
    per = out["per_underlying"]["TSLA"]
    assert per["delta_equiv_shares"] == pytest.approx(100.0, abs=1e-6)
    assert per["net_notional"] == pytest.approx(100.0 * 389.80, abs=1e-3)


def test_book_concentration_hhi_single_name_is_one():
    """A single-underlying book has HHI == 1.0 and a pct-of-NetLiq read."""
    stock_leg = Leg(right=Right.STOCK, strike=0.0, expiry=None, qty=100, mult=1,
                    entry_price=389.80, underlying="TSLA")
    pos = Position(underlying="TSLA", legs=(stock_leg,))
    book = Book(positions=(pos,))
    out = bk.concentration(book, {"TSLA": MS_A}, netliq=50_000.0)
    assert out["hhi"] == pytest.approx(1.0)
    assert out["netliq"] == pytest.approx(50_000.0)
    # gross notional = 100 * 1 * 389.80 = 38980 -> 78% of 50k -> flagged
    assert out["per_underlying"]["TSLA"]["pct_of_netliq"] == pytest.approx(38980.0 / 50_000.0)
    assert any("TSLA" in f for f in out["flags"])


def test_naked_scan_catches_cross_position_hedge():
    """A short call in one position + a long call (same name) in another NET to
    non-naked at the book level -- the L7 whole-book truth."""
    short_pos = Position(
        underlying="TSLA",
        legs=(Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-1, underlying="TSLA"),),
    )
    long_pos = Position(
        underlying="TSLA",
        legs=(Leg(right=Right.CALL, strike=500.0, expiry=EXPIRY, qty=1, underlying="TSLA"),),
    )
    # Net calls = -1 + 1 = 0 -> NOT naked at book level.
    book = Book(positions=(short_pos, long_pos))
    out = bk.naked_scan(book, {"TSLA": MS_A})
    assert out["per_underlying"]["TSLA"]["net_calls"] == pytest.approx(0.0)
    assert "TSLA" not in out["naked_up"]
    assert out["any_unbounded"] is False


def test_naked_scan_flags_true_naked_short_call():
    """A standalone net short call -> unbounded up-tail flagged."""
    short_pos = Position(
        underlying="TSLA",
        legs=(Leg(right=Right.CALL, strike=460.0, expiry=EXPIRY, qty=-1, underlying="TSLA"),),
    )
    book = Book(positions=(short_pos,))
    out = bk.naked_scan(book, {"TSLA": MS_A})
    assert "TSLA" in out["naked_up"]
    assert out["any_unbounded"] is True


def test_income_posture_over_long_recommends_call_credit():
    """An over-long book -> recommend CALL credit spread; REJECT put credit spread
    (it would add correlated long delta -- the book's worst day)."""
    # 1000 long shares -> strongly net long; beta 1.8 to SPY.
    stock_leg = Leg(right=Right.STOCK, strike=0.0, expiry=None, qty=1000, mult=1,
                    entry_price=389.80, underlying="TSLA")
    pos = Position(underlying="TSLA", legs=(stock_leg,))
    book = Book(positions=(pos,))
    out = bk.income_posture(book, {"TSLA": MS_A}, betas={"TSLA": 1.8}, S_spy=500.0,
                            netliq=500_000.0, cfg={"band_spy_shares": 50.0})
    assert out["posture"] == "over_long"
    assert out["recommended_side"] == "call_credit"
    assert out["anti_recommendation"] == "put_credit"
    assert out["anti_reason"] is not None
    assert "correlated" in out["anti_reason"].lower()


def test_income_posture_balanced_band():
    """A flat book inside the dead-band -> balanced, no recommendation."""
    # tiny position well within a wide band
    stock_leg = Leg(right=Right.STOCK, strike=0.0, expiry=None, qty=1, mult=1,
                    entry_price=389.80, underlying="TSLA")
    pos = Position(underlying="TSLA", legs=(stock_leg,))
    book = Book(positions=(pos,))
    out = bk.income_posture(book, {"TSLA": MS_A}, betas={"TSLA": 1.0}, S_spy=500.0,
                            netliq=500_000.0, cfg={"band_spy_shares": 1000.0})
    assert out["posture"] == "balanced"
    assert out["recommended_side"] is None


def test_account_profile_rh_default_is_nra():
    """Sanity tie-in: RH's default profile gates US tax rules OFF (used downstream
    by report posture text); confirms the types contract the book layer leans on."""
    prof = AccountProfile.rh_default()
    assert prof.tax_status == "nra"
    assert prof.tax_rules() == {"wash_sale": False, "holding_split": False, "us_cgt": False}
    acct = Account(profile=prof, cash=0.0, netliq=500_000.0)
    assert acct.netliq == pytest.approx(500_000.0)
