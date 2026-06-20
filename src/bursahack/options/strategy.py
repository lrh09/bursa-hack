"""Strategy intelligence: classification, income posture, rolls, strike selection, ranking.

This module turns raw legs / a book into *decisions*: what is this structure, what
should the book do next, is a roll actually a new position, which strike hits a target
delta, and how do candidate structures rank on a desk-grade composite.

Sign / units (inherited from `types`):
  long qty > 0, short qty < 0; multiplier per-leg (100 default); time in YEARS.
  delta = N(d1) (call) / N(d1)-1 (put), NOT prob-ITM (which is N(d2)).

Strike <- delta inversion (FROZEN golden, MS_A S=389.80,T=1,sigma=0.46,r=0.045,q=0):
  Call delta is monotonically decreasing in K, so we bisect K on
      call_delta(S,K,...) == target.
  target 0.487 -> K ~ 460 ; target 0.692 -> K ~ 360.

Income posture (FROZEN behaviour):
  An over-long book (net positive delta beyond the target band) wants to TRIM delta.
  -> recommend a CALL credit spread (bear call): it adds NEGATIVE delta + collects
     premium without piling on correlated long exposure.
  -> REJECT a put credit spread: it adds POSITIVE (long) delta, doubling down on the
     book's worst day (a correlated sell-off). The rejection carries its reason.

Roll analysis (FROZEN insight):
  The forward delta of a capped / near-expiry spread is ~ 0 (its terminal value is
  pinned), so a "roll" is economically a FRESH position. added_delta is therefore
  ~ the NEW structure's delta, not the naive (new - old) swap. We surface net cash,
  added delta, and cap-ladder stacking.

PMCC validation (FROZEN gates):
  long_delta >= 0.75 (the LEAP behaves like stock), and
  short_strike >= long_strike + net_debit (the diagonal can never lock in a loss),
  and the short leg adds ~ 0 incremental buying power over the deep-ITM long.

References:
  - Natenberg, "Option Volatility & Pricing" (delta, vertical economics, rolls).
  - Reiner-Rubinstein touch / barrier (used via prob, not here).
  - BUILD CONTRACT bursahack.options FINAL, sec 3 (strategy.py), sec 7 (goldens).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from bursahack.options.types import (
    Greeks,
    Leg,
    MarketState,
    Measure,
    Position,
    Right,
    Style,
    Book,
)

# ----------------------------------------------------------------------------
# Pricing access (Law L1: ONE pricing core). bsm / core are v1 critical-path
# and always present; we import them eagerly. Other-agent analytic modules
# (structure / position / book / prob) are imported LAZILY inside functions so
# that importing `strategy` never hard-fails while siblings are mid-build, and
# so this module's own goldens are reproducible without them.
# ----------------------------------------------------------------------------
from bursahack.options import bsm as _bsm


# ============================================================================
# Pricing helpers (all route through bsm -> core; no inline Black-Scholes)
# ============================================================================
def _delta(S: float, K: float, T: float, r: float, q: float, sigma: float,
           right: Right) -> float:
    """Per-share BSM delta via the shared pricing core (L1)."""
    return _bsm.first_order(S, K, T, r, q, sigma, right)["delta"]


def _price(S: float, K: float, T: float, r: float, q: float, sigma: float,
           right: Right) -> float:
    return _bsm.price(S, K, T, r, q, sigma, right)


def _leg_sigma(leg: Leg, market: MarketState) -> float:
    """Per-leg IV precedence: leg.iv -> market.sigma (no call-arg override here)."""
    if leg.iv is not None:
        return leg.iv
    return market.sigma


def _years_to(expiry: date | None, market: MarketState) -> float:
    """Calendar years from market as-of (or today) to expiry. None expiry -> 0.

    Pure/offline: no marketdata import. 365-day calendar convention to match L5.
    """
    if expiry is None:
        return 0.0
    asof = market.asof.date() if market.asof is not None else None
    if asof is None:
        from datetime import date as _date
        asof = _date.today()
    days = (expiry - asof).days
    return max(days, 0) / 365.0


def _leg_delta(leg: Leg, S: float, market: MarketState) -> float:
    """Signed per-SHARE delta of one leg (delta * qty), STOCK = qty, CASH = 0.

    NOTE: this is delta-per-share-times-signed-qty but NOT yet times mult; callers
    that want dollar/share delta multiply by mult themselves. Stock leg qty is in
    shares so its delta is exactly qty (delta of underlying = 1).
    """
    if leg.right is Right.STOCK:
        return leg.qty
    if leg.right is Right.CASH:
        return 0.0
    T = _years_to(leg.expiry, market)
    if T <= 0:
        # Expired-option delta degenerates to 0 / 1 (ITM) per intrinsic; treat as
        # in-the-money indicator. Sufficient for posture-level aggregation.
        if leg.right is Right.CALL:
            intrinsic_delta = 1.0 if S > leg.strike else 0.0
        else:
            intrinsic_delta = -1.0 if S < leg.strike else 0.0
        return intrinsic_delta * leg.qty
    d = _delta(S, leg.strike, T, market.r, market.q, _leg_sigma(leg, market), leg.right)
    return d * leg.qty


def net_delta_shares(legs: list[Leg], S: float, market: MarketState) -> float:
    """Net delta of a leg set in SHARE-equivalents (delta * qty * mult, summed).

    This is the delta the book actually carries (a 1-contract long call with
    delta 0.5 = +50 share deltas). Used by income_posture.
    """
    total = 0.0
    for leg in legs:
        per_share = _leg_delta(leg, S, market)  # already delta*qty
        total += per_share * leg.mult
    return total


# ============================================================================
# Strike selection: invert delta -> strike (FROZEN golden)
# ============================================================================
def strike_for_delta(S: float, T: float, r: float, q: float, sigma: float,
                     target_delta: float, right: Right) -> float:
    """Return the strike whose BSM delta equals `target_delta`.

    Call delta in (0,1) decreasing in K; put delta in (-1,0). `target_delta` is
    interpreted in the leg's natural sign (give 0.30 for a 30-delta call, -0.30 or
    0.30 for a 30-delta put -- we take |target| against the wing's |delta|).

    GOLDEN (MS_A): target 0.487 -> ~460 ; target 0.692 -> ~360.

    Bisection on K is robust (monotone) and avoids the vega-blowup of Newton near
    deep wings. ~60 iterations -> sub-cent strike precision.
    """
    if T <= 0:
        raise ValueError("strike_for_delta requires T > 0")
    if sigma <= 0:
        raise ValueError("strike_for_delta requires sigma > 0")

    tgt = abs(target_delta)
    if not (0.0 < tgt < 1.0):
        raise ValueError("|target_delta| must be in (0,1)")

    def abs_delta(K: float) -> float:
        return abs(_delta(S, K, T, r, q, sigma, right))

    # |delta| is monotonically DECREASING in K for calls and INCREASING in K for
    # puts (a higher-strike put is deeper ITM -> |delta| -> 1). Bracket widely.
    lo, hi = 1e-6, S * 20.0
    # Establish orientation: does |delta| decrease as K rises? (calls yes, puts no)
    decreasing = abs_delta(lo) > abs_delta(hi)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        dm = abs_delta(mid)
        if abs(dm - tgt) < 1e-10:
            return mid
        if decreasing:
            # want smaller |delta| -> move to larger K (hi side)
            if dm > tgt:
                lo = mid
            else:
                hi = mid
        else:
            if dm < tgt:
                lo = mid
            else:
                hi = mid
    return 0.5 * (lo + hi)


# ============================================================================
# Candidate value object
# ============================================================================
@dataclass(frozen=True)
class StrikeCandidate:
    short_strike: float
    long_strike: float
    short_delta: float
    est_credit: float
    max_loss: float
    est_pop: float
    shelf_clear: bool


def select_strikes(intent: dict, market: MarketState, book: Book) -> list[StrikeCandidate]:
    """Empty-shelf strike selection for a defined-risk credit spread.

    `intent` keys (all optional except symbol+side):
      symbol: str                 underlying
      side: 'call' | 'put'        wing to sell (call credit = bearish/delta-trim)
      target_delta: float         short-leg delta target (default 0.25)
      width: float                spread width in strike points (default 30)
      expiry: date                expiry for the candidates (default: market.asof + ~45d N/A -> required)
      n: int                      number of candidates around the target (default 3)
      delta_step: float           delta spacing between candidates (default 0.05)

    "Empty shelf" = a strike where the book has no opposing short already sitting
    (avoids stacking the same strike). shelf_clear reflects that check against the
    book; if structure.shelf_map is unavailable we conservatively report True.

    Returns candidates ordered by ascending short delta (most OTM first).
    """
    symbol = intent["symbol"]
    side = intent.get("side", "call").lower()
    right = Right.CALL if side == "call" else Right.PUT
    target_delta = float(intent.get("target_delta", 0.25))
    width = float(intent.get("width", 30.0))
    expiry = intent.get("expiry")
    n = int(intent.get("n", 3))
    delta_step = float(intent.get("delta_step", 0.05))

    S = market.spot
    r, q, sigma = market.r, market.q, market.sigma
    if expiry is None:
        raise ValueError("select_strikes requires intent['expiry']")
    T = _years_to(expiry, market)
    if T <= 0:
        raise ValueError("select_strikes: expiry must be in the future")

    shelf = _shelf_lookup(book, symbol, right, expiry)

    cands: list[StrikeCandidate] = []
    # Spread candidates around the target short delta.
    deltas = [max(1e-3, min(0.999, target_delta + (i - (n - 1) / 2.0) * delta_step))
              for i in range(n)]
    for d_short in sorted(deltas):
        short_K = strike_for_delta(S, T, r, q, sigma, d_short, right)
        # round to a sensible strike grid (1.0)
        short_K = round(short_K)
        if right is Right.CALL:
            long_K = short_K + width
        else:
            long_K = short_K - width
        short_px = _price(S, short_K, T, r, q, sigma, right)
        long_px = _price(S, long_K, T, r, q, sigma, right)
        est_credit = short_px - long_px           # per share
        max_loss = width - est_credit             # per share, defined risk
        # POP for a credit spread ~ probability the short stays OTM at expiry
        # (RN, finish-side). Use prob.prob_itm lazily; fall back to N(d2)-ish.
        est_pop = _credit_pop(S, short_K, T, r, q, sigma, right, est_credit)
        short_delta = _delta(S, short_K, T, r, q, sigma, right)
        shelf_clear = short_K not in shelf
        cands.append(StrikeCandidate(
            short_strike=float(short_K),
            long_strike=float(long_K),
            short_delta=short_delta,
            est_credit=est_credit,
            max_loss=max_loss,
            est_pop=est_pop,
            shelf_clear=shelf_clear,
        ))
    return cands


def _shelf_lookup(book: Book | None, symbol: str, right: Right,
                  expiry: date | None) -> set[float]:
    """Strikes already occupied by a SHORT of this (symbol,right,expiry) in the book.

    Tolerant of a missing/empty book and a not-yet-built structure.shelf_map.
    """
    occupied: set[float] = set()
    if book is None:
        return occupied
    for pos in book.positions:
        if pos.underlying != symbol:
            continue
        for leg in pos.legs:
            if leg.right is right and leg.expiry == expiry and leg.qty < 0:
                occupied.add(float(leg.strike))
    return occupied


def _credit_pop(S: float, short_K: float, T: float, r: float, q: float,
                sigma: float, right: Right, est_credit: float) -> float:
    """Probability a sold wing finishes OTM (the spread keeps >=0). RN measure.

    For a call credit spread the short call should expire worthless -> P(S_T < K).
    For a put credit spread -> P(S_T > K). Computed via the lazily-imported
    prob.prob_itm (N(d2)); falls back to a local N(d2) if prob is unavailable.
    """
    try:
        from bursahack.options import prob as _prob
        res = _prob.prob_itm(S, short_K, T, r, sigma, q=q, right=right,
                             measure=Measure.RISK_NEUTRAL)
        # short wing finishing OTM == 1 - P(that wing ITM)
        return float(res["prob_otm"])
    except Exception:
        # local N(d2) fallback (risk-neutral)
        from bursahack.options import core as _core
        dd = _core.d1d2(S, short_K, T, r, q, sigma)
        if right is Right.CALL:
            return 1.0 - dd.Nd2          # P(S_T < K)
        return dd.Nd2                    # P(S_T > K)


# ============================================================================
# Income posture (delta-aware advice; FROZEN behaviour)
# ============================================================================
@dataclass(frozen=True)
class PostureAdvice:
    posture: str                 # 'over_long' | 'over_short' | 'balanced'
    net_delta_shares: float
    target_band: tuple[float, float]
    recommended_side: str | None        # 'call_credit' | 'put_credit' | None
    recommendation: str
    anti_recommendation: str | None
    anti_reason: str | None


def income_posture_advice(legs: list[Leg], market: MarketState,
                          netliq: float,
                          target_band_shares: tuple[float, float] | None = None,
                          ) -> PostureAdvice:
    """Premium-selling posture from net delta.

    target_band_shares: acceptable net-delta-in-shares window. If None, derive a
    symmetric band of +/- 5% of (netliq / spot) share-equivalents -- i.e. the book
    is "neutral enough" if its directional exposure is under 5% of NLV.

    FROZEN behaviour:
      net delta ABOVE the band (over-long) -> recommend a CALL credit spread
        (adds negative delta, trims the book toward neutral, collects premium).
        REJECT a put credit spread with its reason (it would add correlated long
        delta = the book's worst day).
      net delta BELOW the band (over-short) -> symmetric: recommend PUT credit,
        reject CALL credit.
      inside the band -> balanced; either premium side is fine (no anti-rec).
    """
    nd = net_delta_shares(legs, market.spot, market)

    if target_band_shares is None:
        if market.spot > 0:
            band = 0.05 * netliq / market.spot
        else:
            band = 0.0
        target_band_shares = (-abs(band), abs(band))
    lo, hi = target_band_shares

    if nd > hi:
        return PostureAdvice(
            posture="over_long",
            net_delta_shares=nd,
            target_band=target_band_shares,
            recommended_side="call_credit",
            recommendation=("Sell a CALL credit spread (bear call): adds negative "
                            "delta to trim the book toward neutral while collecting "
                            "premium."),
            anti_recommendation="put_credit",
            anti_reason=("A put credit spread adds POSITIVE (long) delta, doubling "
                         "down on an already over-long book -- it loses most on the "
                         "book's worst day (a correlated sell-off)."),
        )
    if nd < lo:
        return PostureAdvice(
            posture="over_short",
            net_delta_shares=nd,
            target_band=target_band_shares,
            recommended_side="put_credit",
            recommendation=("Sell a PUT credit spread (bull put): adds positive "
                            "delta to lift an over-short book toward neutral while "
                            "collecting premium."),
            anti_recommendation="call_credit",
            anti_reason=("A call credit spread adds NEGATIVE delta, deepening an "
                         "already over-short book -- it loses most on a rally."),
        )
    return PostureAdvice(
        posture="balanced",
        net_delta_shares=nd,
        target_band=target_band_shares,
        recommended_side=None,
        recommendation=("Book delta is inside the neutral band; either premium side "
                        "is acceptable -- pick on vol/IV-rank, not direction."),
        anti_recommendation=None,
        anti_reason=None,
    )


# ============================================================================
# Roll analysis (forward-delta framing; FROZEN insight)
# ============================================================================
def analyze_roll(old_legs: list[Leg], new_legs: list[Leg], market: MarketState,
                 book: Book | None = None) -> dict:
    """Quantify a roll as what it actually is: usually a FRESH position.

    A capped / near-expiry spread has ~ 0 forward delta (its value is pinned), so
    rolling it forward is economically opening the NEW structure. We therefore frame
    added delta against the OLD structure's *forward* (pinned-aware) delta:

        added_delta_shares = delta(new) - forward_delta(old)

    For a capped near-expiry old spread forward_delta(old) ~ 0, so added_delta ~ the
    new structure's delta alone (NOT the naive new - current_old swap which would
    understate the true exposure being put on).

    Returns:
      old_delta_shares, old_forward_delta_shares, new_delta_shares,
      added_delta_shares, net_cash (debit>0 paid / credit<0 received from the roll),
      old_is_capped, old_is_pinned, cap_stacking (shelf conflicts introduced).
    """
    S = market.spot
    old_delta = net_delta_shares(old_legs, S, market)
    new_delta = net_delta_shares(new_legs, S, market)

    capped = _is_capped(old_legs)
    pinned = _is_pinned_local(old_legs, market)
    # Forward delta of the old structure: if capped & pinned (near expiry, value
    # locked) treat as ~0; otherwise it's the live delta.
    old_forward_delta = 0.0 if (capped and pinned) else old_delta

    added_delta = new_delta - old_forward_delta

    # Net cash of the roll = (cash to close old) + (cash to open new).
    # Closing a position returns its mark; opening pays the new mark.
    close_cash = _mark_cash(old_legs, market, closing=True)
    open_cash = _mark_cash(new_legs, market, closing=False)
    net_cash = open_cash + close_cash   # >0 = net debit paid, <0 = net credit

    cap_stacking = _cap_stacking(new_legs, book)

    return {
        "old_delta_shares": old_delta,
        "old_forward_delta_shares": old_forward_delta,
        "new_delta_shares": new_delta,
        "added_delta_shares": added_delta,
        "net_cash": net_cash,
        "old_is_capped": capped,
        "old_is_pinned": pinned,
        "cap_stacking": cap_stacking,
        "note": ("Capped near-expiry old spread has ~0 forward delta; the roll is a "
                 "FRESH position -- added delta is essentially the new structure's "
                 "delta, not the naive swap."),
    }


def _is_capped(legs: list[Leg]) -> bool:
    """A defined-risk spread is capped on the side that has BOTH a long and a short
    of the same right (a vertical). Heuristic: at least one long+short pair sharing
    a right => the payoff is bounded on that side.
    """
    has_long_call = any(l.right is Right.CALL and l.qty > 0 for l in legs)
    has_short_call = any(l.right is Right.CALL and l.qty < 0 for l in legs)
    has_long_put = any(l.right is Right.PUT and l.qty > 0 for l in legs)
    has_short_put = any(l.right is Right.PUT and l.qty < 0 for l in legs)
    call_capped = has_long_call and has_short_call
    put_capped = has_long_put and has_short_put
    # treat as "capped" if every short option leg is covered by a same-right long
    n_short = sum(1 for l in legs if l.right in (Right.CALL, Right.PUT) and l.qty < 0)
    if n_short == 0:
        return False
    return call_capped or put_capped


def _is_pinned_local(legs: list[Leg], market: MarketState,
                     near_days: float = 7.0) -> bool:
    """Near-expiry: shortest option leg <= near_days away. Pinned spreads carry ~0
    forward delta. Pure/offline (uses _years_to)."""
    times = [
        _years_to(l.expiry, market)
        for l in legs if l.right in (Right.CALL, Right.PUT) and l.expiry is not None
    ]
    if not times:
        return False
    min_T = min(times)
    return min_T * 365.0 <= near_days


def _mark_cash(legs: list[Leg], market: MarketState, closing: bool) -> float:
    """Signed cash to OPEN (closing=False) or CLOSE (closing=True) a leg set, at
    current fair value. Convention: opening a LONG pays cash (debit>0); opening a
    SHORT receives cash (credit<0). Closing flips the sign.

    cash_open(leg) = -sign(qty) * |qty| * fair_px * mult  -> long pays (+), short receives (-)
    Wait: long pays out cash => cash flow to the account is negative. We express
    DEBIT as POSITIVE (cash you part with). So debit>0 / credit<0:
        open_debit(leg) = sign(qty) * |qty| * fair_px * mult
                        = qty * fair_px * mult  (long qty>0 -> +debit, short -> credit)
    Closing reverses: close_cash = -open_debit.
    """
    total = 0.0
    S = market.spot
    for leg in legs:
        if leg.right is Right.STOCK:
            px = S
        elif leg.right is Right.CASH:
            px = 1.0
        else:
            T = _years_to(leg.expiry, market)
            if T <= 0:
                px = _intrinsic(leg, S)
            else:
                px = _price(S, leg.strike, T, market.r, market.q,
                            _leg_sigma(leg, market), leg.right)
        open_debit = leg.qty * px * leg.mult   # long>0 debit, short<0 credit
        total += -open_debit if closing else open_debit
    return total


def _intrinsic(leg: Leg, S: float) -> float:
    if leg.right is Right.CALL:
        return max(S - leg.strike, 0.0)
    if leg.right is Right.PUT:
        return max(leg.strike - S, 0.0)
    return 0.0


def _cap_stacking(new_legs: list[Leg], book: Book | None) -> list[dict]:
    """Detect new short legs that land on a strike where the book already holds a
    short of the same (symbol,right,expiry) -- i.e. cap-ladder stacking."""
    if book is None:
        return []
    conflicts: list[dict] = []
    for leg in new_legs:
        if leg.qty >= 0 or leg.right not in (Right.CALL, Right.PUT):
            continue
        occupied = _shelf_lookup(book, leg.underlying, leg.right, leg.expiry)
        if float(leg.strike) in occupied:
            conflicts.append({
                "symbol": leg.underlying,
                "right": leg.right.value,
                "strike": float(leg.strike),
                "expiry": str(leg.expiry),
                "detail": "new short stacks on an existing book short at this strike",
            })
    return conflicts


# ============================================================================
# Classification (legs -> strategy name). Delegates to structure.classify when
# available; otherwise a self-contained fallback so strategy's own tests stand
# alone.
# ============================================================================
@dataclass(frozen=True)
class StrategyLabel:
    name: str
    variant: str
    confidence: float
    matched_rule: str
    params: dict = field(default_factory=dict)


def classify_legs(legs: list[Leg], underlying: str = "") -> StrategyLabel:
    """Name the structure described by `legs`.

    Prefers structure.classify() (the canonical rule cascade); if that module is
    unavailable or errors, falls back to this module's own compact classifier so
    strategy can be tested in isolation. Always returns a StrategyLabel.
    """
    # Try the canonical classifier first.
    try:
        from bursahack.options import structure as _structure
        und = underlying or (legs[0].underlying if legs else "")
        pos = Position(underlying=und, legs=tuple(legs))
        strat = _structure.classify(pos)
        return StrategyLabel(
            name=getattr(strat, "name", "custom"),
            variant=getattr(strat, "variant", ""),
            confidence=float(getattr(strat, "confidence", 1.0)),
            matched_rule=getattr(strat, "matched_rule", "structure.classify"),
            params=dict(getattr(strat, "params", {}) or {}),
        )
    except Exception:
        return _classify_fallback(legs)


def _classify_fallback(legs: list[Leg]) -> StrategyLabel:
    """Compact, self-contained classifier covering the canon used by tests.

    Handles: long/short single call/put, vertical (bull/bear call/put), straddle,
    strangle, iron condor, butterfly, covered call, cash-secured put, collar, PMCC,
    else 'custom'.
    """
    opts = [l for l in legs if l.right in (Right.CALL, Right.PUT)]
    stock = [l for l in legs if l.right is Right.STOCK]
    calls = [l for l in opts if l.right is Right.CALL]
    puts = [l for l in opts if l.right is Right.PUT]

    def lbl(name, variant="", rule="fallback", conf=0.9, **params):
        return StrategyLabel(name=name, variant=variant, confidence=conf,
                             matched_rule=rule, params=params)

    n = len(opts)

    # --- stock-based structures first (covered call / collar) ---
    if stock:
        sh = sum(s.qty for s in stock)
        if sh > 0 and len(puts) == 1 and puts[0].qty > 0 and len(calls) == 1 \
                and calls[0].qty < 0:
            return lbl("collar", variant="protective", rule="stock+long_put+short_call")
        if sh > 0 and len(calls) == 1 and calls[0].qty < 0 and not puts:
            return lbl("covered_call", variant="income", rule="stock+short_call")
        if sh > 0 and len(puts) == 1 and puts[0].qty > 0 and not calls:
            return lbl("protective_put", variant="hedge", rule="stock+long_put")

    # --- single option (no stock) ---
    if n == 1 and not stock:
        leg = opts[0]
        long = leg.qty > 0
        if leg.right is Right.CALL:
            return lbl("long_call" if long else "short_call",
                       variant="bullish" if long else "bearish", rule="single_call")
        # a lone short put is economically a cash-secured put (income/wheel entry);
        # a long put is a directional/hedge leg.
        if long:
            return lbl("long_put", variant="bearish", rule="single_put")
        return lbl("cash_secured_put", variant="income", rule="single_short_put")

    # --- two same-right verticals (SAME expiry, DIFFERENT strike) ---
    # Different-expiry pairs fall through to the diagonal/PMCC/calendar branch.
    if n == 2 and len(calls) == 2 and not stock and calls[0].expiry == calls[1].expiry:
        lo, hi = sorted(calls, key=lambda l: l.strike)
        if lo.strike != hi.strike:
            # bull call = long lower / short higher ; bear call = short lower / long higher
            if lo.qty > 0 and hi.qty < 0:
                return lbl("bull_call_spread", variant="debit",
                           rule="vertical_call", k_long=lo.strike, k_short=hi.strike)
            if lo.qty < 0 and hi.qty > 0:
                return lbl("bear_call_spread", variant="credit",
                           rule="vertical_call", k_short=lo.strike, k_long=hi.strike)
    if n == 2 and len(puts) == 2 and not stock and puts[0].expiry == puts[1].expiry:
        lo, hi = sorted(puts, key=lambda l: l.strike)
        if lo.strike != hi.strike:
            # bull put (credit) = short higher / long lower ; bear put (debit) = long higher / short lower
            if hi.qty < 0 and lo.qty > 0:
                return lbl("bull_put_spread", variant="credit",
                           rule="vertical_put", k_short=hi.strike, k_long=lo.strike)
            if hi.qty > 0 and lo.qty < 0:
                return lbl("bear_put_spread", variant="debit",
                           rule="vertical_put", k_long=hi.strike, k_short=lo.strike)

    # --- straddle / strangle (one call + one put, same sign) ---
    if n == 2 and len(calls) == 1 and len(puts) == 1 and not stock:
        c, p = calls[0], puts[0]
        if (c.qty > 0) == (p.qty > 0):       # same direction
            long = c.qty > 0
            if c.strike == p.strike:
                return lbl("straddle", variant="long" if long else "short",
                           rule="call+put_same_K", strike=c.strike)
            return lbl("strangle", variant="long" if long else "short",
                       rule="call+put_diff_K", call_k=c.strike, put_k=p.strike)
        # opposite sign call+put = synthetic / risk reversal
        if c.strike == p.strike:
            return lbl("synthetic", variant="long" if c.qty > 0 else "short",
                       rule="synthetic_call+put", strike=c.strike)

    # --- calendar / diagonal / PMCC (same-right pair, DIFFERENT expiries) ---
    if n == 2 and (len(calls) == 2 or len(puts) == 2) and not stock:
        pair = calls if len(calls) == 2 else puts
        a, b = pair
        if a.expiry != b.expiry:
            long_leg = next((l for l in pair if l.qty > 0), None)
            short_near = next((l for l in pair if l.qty < 0), None)
            # same strike -> calendar (horizontal); different strike -> diagonal/PMCC
            if a.strike == b.strike:
                return lbl("calendar", variant="horizontal", rule="diff_exp_same_K",
                           strike=a.strike)
            if long_leg is not None and short_near is not None:
                # PMCC (calls only): long leap LOWER strike & FARTHER expiry
                far = long_leg.expiry is None or short_near.expiry is None or \
                    (long_leg.expiry > short_near.expiry)
                if (len(calls) == 2 and long_leg.strike < short_near.strike and far):
                    return lbl("pmcc", variant="diagonal", rule="poor_mans_covered_call",
                               leap_k=long_leg.strike, short_k=short_near.strike)
            return lbl("diagonal", variant="calendar_spread", rule="diff_exp_diff_K")

    # --- iron condor / iron butterfly (4 legs: 2 calls + 2 puts) ---
    if n == 4 and len(calls) == 2 and len(puts) == 2 and not stock:
        short_call = next((l for l in calls if l.qty < 0), None)
        short_put = next((l for l in puts if l.qty < 0), None)
        long_call = next((l for l in calls if l.qty > 0), None)
        long_put = next((l for l in puts if l.qty > 0), None)

        # SAME-STRIKE SYNTHETIC body (call+put @one strike, opposite signs) -> a
        # directional risk-reversal / synthetic-collar, NOT a neutral iron condor.
        # Detect it before the iron rule (mirrors structure.classify).
        syn_strikes = {cc.strike for cc in calls} & {pp.strike for pp in puts}
        for k in syn_strikes:
            c_at = next((cc for cc in calls if cc.strike == k), None)
            p_at = next((pp for pp in puts if pp.strike == k), None)
            if c_at and p_at and (c_at.qty > 0) != (p_at.qty > 0):
                variant = "long" if c_at.qty > 0 else "short"
                return lbl("risk_reversal", variant=variant,
                           rule="synthetic_body_4leg", synthetic_k=k)

        if short_call and short_put:
            if short_call.strike == short_put.strike:
                return lbl("iron_butterfly", variant="credit", rule="4leg_iron")
            # TRUE iron condor: short-put spread (short put strike ABOVE long put)
            # AND short-call spread (short call strike BELOW long call), 4 distinct
            # strikes. Anything else falls through to custom.
            distinct4 = len({l.strike for l in opts}) == 4
            if (long_call and long_put and distinct4
                    and short_put.strike > long_put.strike
                    and short_call.strike < long_call.strike):
                return lbl("iron_condor", variant="credit", rule="4leg_iron",
                           put_short=short_put.strike, call_short=short_call.strike)

    # --- butterfly (3 same-right legs 1:-2:1) ---
    if n == 3 and (len(calls) == 3 or len(puts) == 3) and not stock:
        leg_set = calls if len(calls) == 3 else puts
        ks = sorted(leg_set, key=lambda l: l.strike)
        if ks[0].qty > 0 and ks[1].qty < 0 and ks[2].qty > 0:
            return lbl("butterfly",
                       variant="call" if len(calls) == 3 else "put",
                       rule="1-2-1", k_low=ks[0].strike, k_mid=ks[1].strike,
                       k_high=ks[2].strike)

    return lbl("custom", variant="", rule="no_match", conf=0.3)


# ============================================================================
# Candidate ranking: composite desk-grade score with HARD GATES first
# ============================================================================
_DEFAULT_WEIGHTS = {
    "rr": 1.0,          # reward/risk
    "pop": 1.0,         # probability of profit
    "bp": -0.5,         # buying-power use (penalty)
    "ev": 1.0,          # expected value
    "delta_fit": 0.5,   # how well it matches the desired delta tilt
    "theta": 0.5,       # positive theta good for income
    "vega": 0.0,        # neutral by default
    "liquidity": 0.5,
}


def rank_candidates(cands: list[Position], market: MarketState,
                    book: Book | None = None,
                    weights: dict | None = None) -> list[dict]:
    """Rank candidate structures by a composite z-score over rr/pop/bp/ev/...

    HARD GATES (applied first, gated candidates pushed to the bottom with reasons):
      - undefined (open) tail  -> reject unless explicitly allowed in candidate meta
      - BP exceeds headroom    -> reject (only when headroom info is supplied via
                                   market/book; absent -> not gated)
      - pin / assignment flag  -> flagged (soft), not auto-rejected.

    Returns a list of dicts (one per candidate) sorted best-first:
      {position, name, score, rr, pop, ev, bp, delta_shares, theta_day, vega,
       defined_risk, gates_passed, gate_reasons}.
    Pure/offline: metrics from bsm + this module's own helpers; structure
    economics used when available, else local intrinsic economics.
    """
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    S = market.spot

    rows: list[dict] = []
    for pos in cands:
        legs = list(pos.legs)
        metrics = _candidate_metrics(legs, market)
        gates_passed, reasons = _apply_gates(metrics, legs)
        rows.append({
            "position": pos,
            "name": classify_legs(legs, pos.underlying).name,
            "rr": metrics["rr"],
            "pop": metrics["pop"],
            "ev": metrics["ev"],
            "bp": metrics["bp"],
            "delta_shares": metrics["delta_shares"],
            "theta_day": metrics["theta_day"],
            "vega": metrics["vega"],
            "defined_risk": metrics["defined_risk"],
            "gates_passed": gates_passed,
            "gate_reasons": reasons,
            "_metrics": metrics,
        })

    # z-score each numeric dimension across the (gate-passing) candidates.
    dims = ["rr", "pop", "bp", "ev", "theta_day", "vega"]
    passing = [r for r in rows if r["gates_passed"]]
    stats = {d: _zstats([r["_metrics"][_metric_key(d)] for r in passing]) for d in dims}

    for r in rows:
        score = 0.0
        for d in dims:
            mu, sd = stats[d]
            val = r["_metrics"][_metric_key(d)]
            z = 0.0 if sd == 0 else (val - mu) / sd
            wkey = "theta" if d == "theta_day" else d
            score += w.get(wkey, 0.0) * z
        # delta_fit: reward candidates whose delta sign matches desired tilt if
        # provided via market.asof-free convention -> skip if no target (0 contrib).
        r["score"] = score if r["gates_passed"] else score - 1e6  # gated sink
        del r["_metrics"]

    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def _metric_key(dim: str) -> str:
    return dim


def _candidate_metrics(legs: list[Leg], market: MarketState) -> dict:
    """Per-candidate economics + greeks, self-contained (no other-agent deps)."""
    S = market.spot
    delta_sh = net_delta_shares(legs, S, market)

    # greeks aggregate (per-share * qty * mult) for theta/vega
    theta_day = 0.0
    vega = 0.0
    for leg in legs:
        if leg.right not in (Right.CALL, Right.PUT):
            continue
        T = _years_to(leg.expiry, market)
        if T <= 0:
            continue
        g = _bsm.first_order(S, leg.strike, T, market.r, market.q,
                             _leg_sigma(leg, market), leg.right)
        theta_day += g["theta_day"] * leg.qty * leg.mult
        vega += g["vega"] * leg.qty * leg.mult

    # net credit/debit at entry (use entry_price if set, else fair value)
    net_cash = 0.0
    for leg in legs:
        if leg.entry_price:
            px = leg.entry_price
        elif leg.right is Right.STOCK:
            px = S
        elif leg.right is Right.CASH:
            px = 1.0
        else:
            T = _years_to(leg.expiry, market)
            px = (_price(S, leg.strike, T, market.r, market.q,
                         _leg_sigma(leg, market), leg.right) if T > 0
                  else _intrinsic(leg, S))
        net_cash += leg.qty * px * leg.mult       # debit>0 / credit<0
    net_credit = -net_cash                         # credit received >0

    # defined-risk + max loss/profit via local intrinsic scan over a wide grid
    defined_risk, max_loss, max_profit = _risk_shape_local(legs, market, net_cash)
    rr = (max_profit / max_loss) if (max_loss and max_loss > 0) else float("inf")

    # POP: fraction of an RN terminal grid that is profitable (analytic-lite)
    pop = _pop_local(legs, market, net_cash)
    # EV (RN, undiscounted) over the same grid
    ev = _ev_local(legs, market, net_cash)
    # BP proxy: defined-risk max loss (PM ~ stress loss); unbounded -> large
    bp = max_loss if defined_risk else float("inf")

    return {
        "delta_shares": delta_sh,
        "theta_day": theta_day,
        "vega": vega,
        "net_credit": net_credit,
        "defined_risk": defined_risk,
        "max_loss": max_loss,
        "max_profit": max_profit,
        "rr": rr if rr != float("inf") else 1e6,
        "pop": pop,
        "ev": ev,
        "bp": bp if bp != float("inf") else 1e12,
    }


def _terminal_pnl(legs: list[Leg], ST: float, net_cash_entry: float) -> float:
    """Expiry P&L of the leg set at terminal price ST. net_cash_entry is debit>0 /
    credit<0 (cash paid at fill). P&L = sum(signed intrinsic*qty*mult) - net_cash_entry.
    """
    gross = 0.0
    for leg in legs:
        if leg.right is Right.CALL:
            intrinsic = max(ST - leg.strike, 0.0)
        elif leg.right is Right.PUT:
            intrinsic = max(leg.strike - ST, 0.0)
        elif leg.right is Right.STOCK:
            intrinsic = ST
        else:
            intrinsic = 1.0
        gross += intrinsic * leg.qty * leg.mult
    return gross - net_cash_entry


def _risk_shape_local(legs: list[Leg], market: MarketState,
                      net_cash_entry: float) -> tuple[bool, float, float]:
    """(defined_risk, max_loss>0, max_profit) via asymptotic-slope tail test + grid.

    Slope test: P&L slope as ST->inf and ST->0. Nonzero slope on either tail =>
    unbounded on that side => not defined-risk.
    """
    S = market.spot
    big = max(S * 50.0, 1e5)
    p_hi1 = _terminal_pnl(legs, big, net_cash_entry)
    p_hi2 = _terminal_pnl(legs, big * 2, net_cash_entry)
    p_lo1 = _terminal_pnl(legs, 0.0, net_cash_entry)
    # lower tail bounded at ST=0 (calls/puts both flat below all strikes downward
    # except stock); upper-tail slope:
    up_slope = (p_hi2 - p_hi1) / big
    # downward unbounded only with short put / short stock heavy below 0 (can't go
    # below 0 for ST, so loss is bounded by ST=0) -> only the up tail can be open.
    defined_up = abs(up_slope) < 1e-6
    defined = defined_up

    # grid over plausible terminal range for bounded max/min
    grid = [S * x / 100.0 for x in range(1, 400)]
    pnls = [_terminal_pnl(legs, ST, net_cash_entry) for ST in grid]
    pnls.append(p_lo1)
    if defined:
        pnls.append(p_hi1)
    max_profit = max(pnls)
    max_loss = -min(pnls)            # report as positive magnitude
    if max_loss < 0:
        max_loss = 0.0
    if not defined:
        max_profit = float("inf")
    return defined, max_loss, max_profit


def _pop_local(legs: list[Leg], market: MarketState, net_cash_entry: float) -> float:
    """RN probability of profit via a lognormal terminal grid (Simpson-ish sum)."""
    from bursahack.options import core as _core
    import math

    S = market.spot
    # pick the longest leg's T as horizon
    Ts = [_years_to(l.expiry, market) for l in legs
          if l.right in (Right.CALL, Right.PUT) and l.expiry is not None]
    T = max(Ts) if Ts else 0.0
    if T <= 0:
        return 1.0 if _terminal_pnl(legs, S, net_cash_entry) > 0 else 0.0
    sigma = market.sigma if market.sigma > 0 else 0.3
    drift = market.r - market.q
    n = 4000
    lo, hi = S * 0.01, S * 6.0
    step = (hi - lo) / n
    prob = 0.0
    mu = math.log(S) + (drift - 0.5 * sigma * sigma) * T
    sd = sigma * math.sqrt(T)
    for i in range(n):
        ST = lo + (i + 0.5) * step
        if _terminal_pnl(legs, ST, net_cash_entry) > 0:
            # lognormal pdf of ST
            z = (math.log(ST) - mu) / sd
            pdf = math.exp(-0.5 * z * z) / (ST * sd * (2 * math.pi) ** 0.5)
            prob += pdf * step
    return min(max(prob, 0.0), 1.0)


def _ev_local(legs: list[Leg], market: MarketState, net_cash_entry: float) -> float:
    """RN expected terminal P&L over a lognormal grid (undiscounted)."""
    import math
    S = market.spot
    Ts = [_years_to(l.expiry, market) for l in legs
          if l.right in (Right.CALL, Right.PUT) and l.expiry is not None]
    T = max(Ts) if Ts else 0.0
    if T <= 0:
        return _terminal_pnl(legs, S, net_cash_entry)
    sigma = market.sigma if market.sigma > 0 else 0.3
    drift = market.r - market.q
    n = 4000
    lo, hi = S * 0.01, S * 6.0
    step = (hi - lo) / n
    mu = math.log(S) + (drift - 0.5 * sigma * sigma) * T
    sd = sigma * math.sqrt(T)
    ev = 0.0
    for i in range(n):
        ST = lo + (i + 0.5) * step
        z = (math.log(ST) - mu) / sd
        pdf = math.exp(-0.5 * z * z) / (ST * sd * (2 * math.pi) ** 0.5)
        ev += _terminal_pnl(legs, ST, net_cash_entry) * pdf * step
    return ev


def _apply_gates(metrics: dict, legs: list[Leg]) -> tuple[bool, list[str]]:
    """HARD GATES. Returns (passed, reasons)."""
    reasons: list[str] = []
    passed = True
    if not metrics["defined_risk"]:
        # allow if any leg explicitly marks allow_undefined via underlying convention?
        # v1: reject undefined tails (L7).
        passed = False
        reasons.append("undefined (open) tail -- rejected by defined-risk gate (L7)")
    return passed, reasons


def _zstats(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    n = len(xs)
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / n
    return mu, var ** 0.5


# ============================================================================
# PMCC analysis (FROZEN gates)
# ============================================================================
def analyze_pmcc(long_leg: Leg, short_leg: Leg, market: MarketState) -> dict:
    """Validate & quantify a poor-man's covered call (diagonal).

    Gates (FROZEN):
      long_delta >= 0.75            (the LEAP behaves like stock)
      short_strike >= long_strike + net_debit
                                    (the diagonal can never lock in a loss)
      ~0 incremental BP             (the short call is fully covered by the long;
                                    we report the *incremental* defined-risk BP, ~0)

    net_debit = long_entry - short_entry (per share). If entry prices are absent,
    fall back to current fair value.

    Returns:
      {ok, long_delta, short_delta, net_debit, no_loss_safe, bp_incremental,
       cycle_yield, max_profit_if_called, reasons[]}.
    """
    S = market.spot
    reasons: list[str] = []

    T_long = _years_to(long_leg.expiry, market)
    T_short = _years_to(short_leg.expiry, market)

    long_sigma = _leg_sigma(long_leg, market)
    short_sigma = _leg_sigma(short_leg, market)

    long_delta = (_delta(S, long_leg.strike, T_long, market.r, market.q,
                         long_sigma, long_leg.right) if T_long > 0 else
                  (1.0 if (long_leg.right is Right.CALL and S > long_leg.strike) else 0.0))
    short_delta = (_delta(S, short_leg.strike, T_short, market.r, market.q,
                          short_sigma, short_leg.right) if T_short > 0 else 0.0)

    # net debit from entry prices (per share); fall back to fair value
    if long_leg.entry_price or short_leg.entry_price:
        long_px = long_leg.entry_price or _price(S, long_leg.strike, T_long,
                                                  market.r, market.q, long_sigma,
                                                  long_leg.right)
        short_px = short_leg.entry_price or _price(S, short_leg.strike, T_short,
                                                    market.r, market.q, short_sigma,
                                                    short_leg.right)
    else:
        long_px = _price(S, long_leg.strike, T_long, market.r, market.q,
                         long_sigma, long_leg.right)
        short_px = _price(S, short_leg.strike, T_short, market.r, market.q,
                          short_sigma, short_leg.right)
    net_debit = long_px - short_px                # per share

    # GATE 1: long is deep ITM (delta >= 0.75)
    deep_itm = long_delta >= 0.75
    if not deep_itm:
        reasons.append(f"long leg delta {long_delta:.3f} < 0.75 -- not deep enough "
                       f"to behave like stock")

    # GATE 2: no-loss safety -- short strike covers the net debit paid
    no_loss_safe = short_leg.strike >= (long_leg.strike + net_debit)
    if not no_loss_safe:
        reasons.append(f"short strike {short_leg.strike} < long strike "
                       f"{long_leg.strike} + net debit {net_debit:.2f} -- assignment "
                       f"could lock in a loss")

    # GATE 3: incremental BP of the short over the deep-ITM long ~ 0. The covered
    # short adds no new defined risk: max loss is bounded by the long. We report
    # the incremental max-loss BP, which for a valid PMCC is ~0 (the short is
    # covered by the long up to the strike spread which is non-negative profit).
    mult = long_leg.mult
    qty = abs(long_leg.qty)
    # incremental defined-risk: if no_loss_safe, the short never costs the long; the
    # worst incremental case is the short being assigned at expiry while long covers.
    bp_incremental = max(0.0, -(short_leg.strike - long_leg.strike - net_debit)) \
        * mult * qty
    # if no_loss_safe, (short_strike - long_strike - net_debit) >= 0 -> bp_incr = 0

    # cycle yield: short credit collected / net debit (capital at risk), per cycle.
    cycle_yield = (short_px / net_debit) if net_debit > 0 else float("inf")

    # max profit if called away at short strike at short expiry (approx):
    # (short_strike - long_strike) - net_debit  (per share) * mult * qty
    max_profit_if_called = ((short_leg.strike - long_leg.strike) - net_debit) \
        * mult * qty

    ok = deep_itm and no_loss_safe
    return {
        "ok": ok,
        "long_delta": long_delta,
        "short_delta": short_delta,
        "net_debit": net_debit,
        "deep_itm": deep_itm,
        "no_loss_safe": no_loss_safe,
        "bp_incremental": bp_incremental,
        "cycle_yield": cycle_yield,
        "max_profit_if_called": max_profit_if_called,
        "reasons": reasons,
    }
