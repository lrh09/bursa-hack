"""Strategy structure layer: named builders, classification, and economics.

This is the *structure* boundary of the options toolkit — the inverse pair of
``build`` and ``classify`` plus the position-level ``economics`` summary that the
scenario engine, the report renderer, and the CLI all compose against.

Three responsibilities, all pure / offline:

  1. **Builders** — every named-strategy constructor lives canonically in
     :mod:`bursahack.options.instruments`; this module RE-EXPORTS them (plus
     ``from_legs``) so callers can ``from bursahack.options.structure import
     iron_condor`` and so ``bursahack.options.__init__`` can surface the headline
     builders from one place. No re-implementation — single source of truth.

  2. **classify(legs_or_position) -> StrategyClass** — name the structure a leg
     set describes (vertical / straddle / strangle / condor / butterfly /
     calendar / diagonal / pmcc / collar / covered-call / synthetic / ratio /
     single / custom). Returns a frozen value object exposing ``.name`` /
     ``.variant`` / ``.confidence`` / ``.matched_rule`` / ``.params`` — the shape
     :mod:`strategy` and the report/CLI consumers read. It is INDEPENDENT of
     ``strategy.classify_legs`` (that delegates here; recursing back would loop).

  3. **economics(legs_or_position, market, s_grid) -> EconSheet** — the
     position-level economics summary: net premium (entry debit>0 / credit<0),
     breakevens, max profit / max loss, strike width, reward:risk, capital at
     risk, the expiry P&L ladder over ``s_grid``, and the net per-share greeks.
     Built on the TESTED risk lens (:func:`risk.defined_risk` /
     :func:`risk.net_cost_entry` / :func:`risk.defined_risk_margin`) so the
     max-loss / breakeven numbers come from the golden-locked source, never a
     parallel re-derivation. Returns a frozen dataclass whose FIELD NAMES match
     both the attribute access in :mod:`scenario` / :mod:`report` and the dict
     keys the CLI digs out via ``dataclasses.asdict``.

Sign / unit conventions (inherited from :mod:`bursahack.options.types`):
  long qty > 0, short qty < 0; per-leg multiplier (``leg.mult``, 100 default);
  time in YEARS; entry_price a per-share magnitude (cash-flow sign from qty).
  net premium / max-loss / max-profit / ladder values are POSITION DOLLARS
  (already ``* mult * qty``); breakevens / width are in price points.

References:
  - Hull, "Options, Futures, and Other Derivatives" (combinations, parity).
  - Natenberg, "Option Volatility & Pricing" (vertical economics, R:R).
  - Build contract §3 (structure.py: classify + economics).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bursahack.options.types import Leg, MarketState, Right

# --- Re-export the canonical named-strategy builders (single source of truth) -
# instruments.py owns the constructors; we surface them from the structure
# namespace so consumers + bursahack.options.__init__ import them from one place.
from bursahack.options.instruments import (  # noqa: F401  (re-export surface)
    bear_call_spread,
    bear_put_spread,
    bull_call_spread,
    bull_put_spread,
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

__all__ = [
    # re-exported builders
    "long_call",
    "long_put",
    "short_call",
    "short_put",
    "vertical",
    "bull_call_spread",
    "bear_call_spread",
    "bull_put_spread",
    "bear_put_spread",
    "straddle",
    "strangle",
    "iron_condor",
    "iron_butterfly",
    "butterfly",
    "calendar",
    "diagonal",
    "collar",
    "covered_call",
    "cash_secured_put",
    "pmcc",
    "ratio_spread",
    "synthetic_long",
    "synthetic_short",
    "from_legs",
    # value objects produced here
    "StrategyClass",
    "EconSheet",
    # functions
    "classify",
    "economics",
    "summary_card",
]


# ===========================================================================
# Leg coercion
# ===========================================================================
def _resolve_legs(legs_or_position) -> list[Leg]:
    """Accept a Position (has ``.legs``) or a raw list / tuple of Leg."""
    inner = getattr(legs_or_position, "legs", None)
    if inner is not None:
        return list(inner)
    return list(legs_or_position)


# ===========================================================================
# Classification value object + rule cascade
# ===========================================================================
@dataclass(frozen=True)
class StrategyClass:
    """The name + metadata of a classified structure.

    Field shape matches what :mod:`strategy` (``classify_legs``) and the
    report/CLI consumers read: ``.name`` / ``.variant`` / ``.confidence`` /
    ``.matched_rule`` / ``.params``.
    """

    name: str
    variant: str = ""
    confidence: float = 1.0
    matched_rule: str = ""
    params: dict = field(default_factory=dict)


def _lbl(name: str, variant: str = "", rule: str = "", conf: float = 0.95,
         **params) -> StrategyClass:
    return StrategyClass(name=name, variant=variant, confidence=conf,
                         matched_rule=rule, params=params)


def classify(legs_or_position) -> StrategyClass:
    """Name the structure described by a leg set (or Position).

    A compact, self-contained rule cascade covering the package canon: single
    call/put, the four verticals, straddle / strangle, iron condor / butterfly,
    single-right butterfly, calendar / diagonal / PMCC, collar / covered-call /
    protective-put, synthetic long/short, ratio spread, else ``custom``. Returns a
    :class:`StrategyClass`. Pure: no market data, no pricing.

    This is the canonical classifier; :func:`strategy.classify_legs` delegates to
    it (so it must never call back into ``strategy`` — that would recurse).
    """
    legs = _resolve_legs(legs_or_position)
    opts = [l for l in legs if l.right in (Right.CALL, Right.PUT)]
    stock = [l for l in legs if l.right is Right.STOCK]
    calls = [l for l in opts if l.right is Right.CALL]
    puts = [l for l in opts if l.right is Right.PUT]
    n = len(opts)

    # --- stock-overlay structures first (covered call / collar / prot. put) ---
    if stock:
        sh = sum(s.qty for s in stock)
        if sh > 0 and len(puts) == 1 and puts[0].qty > 0 and len(calls) == 1 \
                and calls[0].qty < 0:
            return _lbl("collar", "protective", "stock+long_put+short_call")
        if sh > 0 and len(calls) == 1 and calls[0].qty < 0 and not puts:
            return _lbl("covered_call", "income", "stock+short_call")
        if sh > 0 and len(puts) == 1 and puts[0].qty > 0 and not calls:
            return _lbl("protective_put", "hedge", "stock+long_put")

    # --- single option (no stock) ---
    if n == 1 and not stock:
        leg = opts[0]
        long = leg.qty > 0
        if leg.right is Right.CALL:
            return _lbl("long_call" if long else "short_call",
                        "bullish" if long else "bearish", "single_call",
                        strike=leg.strike)
        # lone short put ~ cash-secured put (income / wheel); long put = hedge.
        if long:
            return _lbl("long_put", "bearish", "single_put", strike=leg.strike)
        return _lbl("cash_secured_put", "income", "single_short_put",
                    strike=leg.strike)

    # --- two same-right verticals (SAME expiry, DIFFERENT strike) ---
    if n == 2 and len(calls) == 2 and not stock and calls[0].expiry == calls[1].expiry:
        lo, hi = sorted(calls, key=lambda l: l.strike)
        if lo.strike != hi.strike:
            if lo.qty > 0 and hi.qty < 0:
                return _lbl("bull_call_spread", "debit", "vertical_call",
                            k_long=lo.strike, k_short=hi.strike)
            if lo.qty < 0 and hi.qty > 0:
                return _lbl("bear_call_spread", "credit", "vertical_call",
                            k_short=lo.strike, k_long=hi.strike)
    if n == 2 and len(puts) == 2 and not stock and puts[0].expiry == puts[1].expiry:
        lo, hi = sorted(puts, key=lambda l: l.strike)
        if lo.strike != hi.strike:
            if hi.qty < 0 and lo.qty > 0:
                return _lbl("bull_put_spread", "credit", "vertical_put",
                            k_short=hi.strike, k_long=lo.strike)
            if hi.qty > 0 and lo.qty < 0:
                return _lbl("bear_put_spread", "debit", "vertical_put",
                            k_long=hi.strike, k_short=lo.strike)

    # --- straddle / strangle / synthetic (one call + one put) ---
    if n == 2 and len(calls) == 1 and len(puts) == 1 and not stock:
        c, p = calls[0], puts[0]
        if (c.qty > 0) == (p.qty > 0):                 # same direction
            long = c.qty > 0
            if c.strike == p.strike:
                return _lbl("straddle", "long" if long else "short",
                            "call+put_same_K", strike=c.strike)
            return _lbl("strangle", "long" if long else "short",
                        "call+put_diff_K", call_k=c.strike, put_k=p.strike)
        # opposite sign call+put = synthetic / risk reversal
        if c.strike == p.strike:
            return _lbl("synthetic", "long" if c.qty > 0 else "short",
                        "synthetic_call+put", strike=c.strike)
        return _lbl("risk_reversal", "long" if c.qty > 0 else "short",
                    "call+put_opp_sign", call_k=c.strike, put_k=p.strike)

    # --- calendar / diagonal / PMCC (same-right pair, DIFFERENT expiries) ---
    if n == 2 and (len(calls) == 2 or len(puts) == 2) and not stock:
        pair = calls if len(calls) == 2 else puts
        a, b = pair
        if a.expiry != b.expiry:
            long_leg = next((l for l in pair if l.qty > 0), None)
            short_near = next((l for l in pair if l.qty < 0), None)
            if a.strike == b.strike:
                return _lbl("calendar", "horizontal", "diff_exp_same_K",
                            strike=a.strike)
            if long_leg is not None and short_near is not None:
                far = (long_leg.expiry is None or short_near.expiry is None
                       or long_leg.expiry > short_near.expiry)
                if len(calls) == 2 and long_leg.strike < short_near.strike and far:
                    return _lbl("pmcc", "diagonal", "poor_mans_covered_call",
                                leap_k=long_leg.strike, short_k=short_near.strike)
            return _lbl("diagonal", "calendar_spread", "diff_exp_diff_K")

    # --- ratio spread (same-right, two legs, unequal |qty|) ---
    if n == 2 and (len(calls) == 2 or len(puts) == 2) and not stock:
        pair = calls if len(calls) == 2 else puts
        a, b = pair
        if a.expiry == b.expiry and a.strike != b.strike \
                and (a.qty > 0) != (b.qty > 0) and abs(a.qty) != abs(b.qty):
            return _lbl("ratio_spread",
                        "call" if len(calls) == 2 else "put",
                        "ratio_unequal_qty")

    # --- iron condor / iron butterfly (4 legs: 2 calls + 2 puts) ---
    if n == 4 and len(calls) == 2 and len(puts) == 2 and not stock:
        short_call_leg = next((l for l in calls if l.qty < 0), None)
        short_put_leg = next((l for l in puts if l.qty < 0), None)
        long_call_leg = next((l for l in calls if l.qty > 0), None)
        long_put_leg = next((l for l in puts if l.qty > 0), None)

        # (a) SAME-STRIKE SYNTHETIC body? A call+put at one strike with opposite
        # signs is a synthetic long/short forward — the structure is a directional
        # risk-reversal / synthetic-collar (net long or short delta), NOT a
        # delta-neutral iron condor. Detect it BEFORE the iron rule so a bullish
        # combo (+K C / −K P + OTM short-call wing + OTM long-put floor) is never
        # mislabeled. (E.g. +470C/−470P/−600C/+380P -> synthetic long @470.)
        syn_strikes = {c.strike for c in calls} & {p.strike for p in puts}
        for k in syn_strikes:
            c_at = next((c for c in calls if c.strike == k), None)
            p_at = next((p for p in puts if p.strike == k), None)
            if c_at and p_at and (c_at.qty > 0) != (p_at.qty > 0):
                # synthetic LONG  = long call + short put @K (net +delta);
                # synthetic SHORT = short call + long put @K (net −delta).
                variant = "long" if c_at.qty > 0 else "short"
                return _lbl("risk_reversal", variant, "synthetic_body_4leg",
                            synthetic_k=k)

        if short_call_leg and short_put_leg:
            if short_call_leg.strike == short_put_leg.strike:
                return _lbl("iron_butterfly", "credit", "4leg_iron",
                            body=short_call_leg.strike)
            # TRUE iron condor geometry: a short-PUT spread (short put strike ABOVE
            # the long put strike) AND a short-CALL spread (short call strike BELOW
            # the long call strike), across 4 distinct strikes with the shorts
            # straddling spot. Anything else (e.g. a long call spread paired with a
            # synthetic) is NOT an iron condor -> fall through to custom.
            distinct4 = len({l.strike for l in opts}) == 4
            if (long_call_leg and long_put_leg and distinct4
                    and short_put_leg.strike > long_put_leg.strike
                    and short_call_leg.strike < long_call_leg.strike):
                return _lbl("iron_condor", "credit", "4leg_iron",
                            put_short=short_put_leg.strike,
                            call_short=short_call_leg.strike)

    # --- butterfly (3 same-right legs 1:-2:1) ---
    if n == 3 and (len(calls) == 3 or len(puts) == 3) and not stock:
        leg_set = calls if len(calls) == 3 else puts
        ks = sorted(leg_set, key=lambda l: l.strike)
        if ks[0].qty > 0 and ks[1].qty < 0 and ks[2].qty > 0:
            return _lbl("butterfly", "call" if len(calls) == 3 else "put", "1-2-1",
                        k_low=ks[0].strike, k_mid=ks[1].strike, k_high=ks[2].strike)
        if ks[0].qty < 0 and ks[1].qty > 0 and ks[2].qty < 0:
            return _lbl("butterfly", "short", "1-2-1_inverted",
                        k_low=ks[0].strike, k_mid=ks[1].strike, k_high=ks[2].strike)

    return _lbl("custom", "", "no_match", conf=0.3)


# ===========================================================================
# Economics summary value object + computation
# ===========================================================================
@dataclass(frozen=True)
class EconSheet:
    """Position-level economics summary.

    FIELD NAMES are load-bearing: :mod:`scenario` and :mod:`report` read them as
    attributes (``econ.net_premium`` etc.), and the CLI digs the same names out
    via ``dataclasses.asdict``. ``expiry_pl_ladder`` maps ``{S: pnl}`` over the
    supplied ``s_grid``. All P&L values are POSITION DOLLARS (``* mult * qty``);
    ``breakevens`` / ``width`` are in price points.
    """

    net_premium: float                       # entry debit>0 / credit<0 (dollars)
    breakevens: tuple[float, ...]
    max_profit: float | None                 # None when unbounded up-tail
    max_loss: float | None                   # positive magnitude; None if unbounded
    width: float | None                      # strike width (points) or None
    rr: float | None                         # reward : risk (max_profit / max_loss)
    capital: float | None                    # capital at risk / BP requirement
    expiry_pl_ladder: dict                    # {S: total expiry P&L}
    net_greeks: dict = field(default_factory=dict)
    defined_risk: bool = True
    unbounded_side: str | None = None


def _intrinsic_per_share(leg: Leg, S: float) -> float:
    """Per-share intrinsic value of one leg at spot ``S`` (>= 0, unsigned)."""
    if leg.right is Right.CALL:
        return max(S - leg.strike, 0.0)
    if leg.right is Right.PUT:
        return max(leg.strike - S, 0.0)
    if leg.right is Right.STOCK:
        return S
    return 1.0  # CASH unit


def _gross_terminal(legs: list[Leg], S: float) -> float:
    """Gross terminal value of the leg set at ``S`` (signed by qty, * mult)."""
    return sum(_intrinsic_per_share(lg, S) * lg.qty * lg.mult for lg in legs)


def _expiry_ladder(legs: list[Leg], s_grid, net_cost: float) -> dict:
    """``{S: total expiry P&L}`` over ``s_grid``.

    Prefers the sibling ``payoff`` module (the consumer-camp single-source payoff)
    when present so the ladder is byte-identical to the rest of the engine; falls
    back to the local intrinsic computation while ``payoff`` is mid-build. Both
    paths yield ``gross_terminal(S) - net_cost`` per the L3 payoff convention.
    """
    points = [float(s) for s in s_grid]
    try:
        from bursahack.options import payoff as _payoff  # lazy: consumer-glue
        ladder = _payoff.expiry_ladder(legs, points, net_cost)
        table = ladder.get("pnl_total", ladder) if isinstance(ladder, dict) else ladder
        if isinstance(table, dict) and table:
            return {float(k): float(v) for k, v in table.items()}
    except Exception:
        pass
    return {S: _gross_terminal(legs, S) - net_cost for S in points}


def _net_cost_entry(legs: list[Leg]) -> float:
    """Signed net entry cash (debit>0 / credit<0).

    Prefers the canonical ``position.net_cost_entry`` (consumer-camp glue) when
    present; otherwise uses the TESTED ``risk.net_cost_entry`` (the math-camp
    mirror); finally a local sum of per-leg signed cashflows. All three agree by
    contract (``qty * entry_price * mult``).
    """
    try:
        from bursahack.options import position as _position  # lazy: consumer-glue
        return float(_position.net_cost_entry(legs))
    except Exception:
        pass
    try:
        from bursahack.options import risk as _risk  # tested math-camp mirror
        return float(_risk.net_cost_entry(legs))
    except Exception:
        return float(sum(lg.signed_cashflow_entry() for lg in legs))


def _strike_width(legs: list[Leg]) -> float | None:
    """Strike width of a single same-right one-wide vertical, else None.

    Two option legs, same right, equal & opposite qty -> ``|K1 - K2|``. Returns
    None for anything that is not a clean vertical (the only shape with a single
    well-defined width).
    """
    opt = [l for l in legs if l.right in (Right.CALL, Right.PUT)]
    if len(opt) != 2:
        return None
    a, b = opt
    if a.right is not b.right:
        return None
    if abs(a.qty) != abs(b.qty):
        return None
    if (a.qty > 0) == (b.qty > 0):
        return None
    return abs(a.strike - b.strike)


def _net_greeks(legs: list[Leg], market: MarketState) -> dict:
    """Net per-share aggregate greeks of the leg set (best-effort, offline).

    Uses :mod:`strategy`'s tested per-leg greek path (which routes through the
    shared pricing core) to sum ``delta`` (share-equivalent) and ``theta_day`` /
    ``vega`` (``* qty * mult``). Returns an empty dict if the pricing path is
    unavailable (e.g. zero vol / no expiry). Never raises.
    """
    out: dict[str, float] = {}
    try:
        from bursahack.options import strategy as _strategy  # tested greek path
        out["delta"] = float(_strategy.net_delta_shares(legs, market.spot, market))
    except Exception:
        return out

    theta_day = 0.0
    vega = 0.0
    gamma = 0.0
    try:
        from bursahack.options import bs as _bs
        for leg in legs:
            if leg.right not in (Right.CALL, Right.PUT):
                continue
            T = _strategy._years_to(leg.expiry, market)
            if T <= 0:
                continue
            sigma = leg.iv if leg.iv is not None else market.sigma
            if sigma <= 0:
                continue
            g = _bs.first_order(market.spot, leg.strike, T, market.r, market.q,
                                sigma, leg.right)
            theta_day += g["theta_day"] * leg.qty * leg.mult
            vega += g["vega"] * leg.qty * leg.mult
            gamma += g["gamma"] * leg.qty * leg.mult
        out["theta_day"] = theta_day
        out["vega"] = vega
        out["gamma"] = gamma
    except Exception:
        pass
    return out


def economics(legs_or_position, market: MarketState, s_grid) -> EconSheet:
    """Position-level economics: premium, breakevens, max P/L, width, R:R, ladder.

    Args:
        legs_or_position: a Position or a raw list[Leg].
        market: the MarketState (spot / r / q / sigma / asof) — used for the net
            greeks; the payoff-level numbers are market-independent.
        s_grid: spot points for the expiry P&L ladder.

    The max-loss / max-profit / breakevens come from the TESTED defined-risk lens
    (:func:`risk.defined_risk`), and the capital from
    :func:`risk.defined_risk_margin`, so this never re-derives golden-locked
    numbers. Returns a frozen :class:`EconSheet`.
    """
    legs = _resolve_legs(legs_or_position)
    net_cost = _net_cost_entry(legs)
    ladder = _expiry_ladder(legs, s_grid, net_cost)
    width = _strike_width(legs)
    net_greeks = _net_greeks(legs, market)

    # Defined-risk lens: golden-locked max-loss / max-profit / breakevens.
    max_profit: float | None = None
    max_loss: float | None = None
    breakevens: tuple[float, ...] = ()
    defined = True
    unbounded_side: str | None = None
    try:
        from bursahack.options import risk as _risk
        rep = _risk.defined_risk(legs)
        defined = bool(rep.defined_risk)
        unbounded_side = rep.unbounded_side
        breakevens = tuple(float(b) for b in rep.breakevens)
        max_profit = _num_or_none(rep.max_profit)
        max_loss = _num_or_none(rep.max_loss)
    except Exception:
        # Fall back to the ladder extrema (bounded read over the supplied grid).
        if ladder:
            vals = list(ladder.values())
            max_profit = max(vals)
            mn = min(vals)
            max_loss = -mn if mn < 0 else 0.0

    # Capital at risk / BP requirement (defined-risk margin estimate).
    capital: float | None = None
    try:
        from bursahack.options import risk as _risk
        est = _risk.defined_risk_margin(legs)
        req = est.requirement
        capital = None if (req == float("inf") or req != req) else float(req)
    except Exception:
        capital = max_loss

    rr: float | None = None
    if (max_profit is not None and max_loss is not None
            and max_loss > 0 and max_profit not in (None,)):
        rr = max_profit / max_loss

    return EconSheet(
        net_premium=net_cost,
        breakevens=breakevens,
        max_profit=max_profit,
        max_loss=max_loss,
        width=width,
        rr=rr,
        capital=capital,
        expiry_pl_ladder=ladder,
        net_greeks=net_greeks,
        defined_risk=defined,
        unbounded_side=unbounded_side,
    )


def _num_or_none(x) -> float | None:
    """Coerce a defined-risk field to float, or None for the 'unbounded' sentinel."""
    if isinstance(x, str):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# Summary card (CLI / report convenience)
# ===========================================================================
def summary_card(legs_or_position, market: MarketState, s_grid=None) -> dict:
    """One compact dict combining classification + economics for a position.

    The CLI calls ``structure.summary_card(legs, mkt)`` to print a per-position
    line. Pure / defensive: every field degrades to a sensible default rather than
    raising. ``s_grid`` defaults to a symmetric band around spot (incl. strikes)
    so breakevens / ladder are sampled exactly.
    """
    legs = _resolve_legs(legs_or_position)
    if s_grid is None:
        s_grid = _default_grid(market.spot, legs)
    cls = classify(legs)
    econ = economics(legs, market, s_grid)
    return {
        "name": cls.name,
        "variant": cls.variant,
        "matched_rule": cls.matched_rule,
        "net_premium": econ.net_premium,
        "breakevens": econ.breakevens,
        "max_profit": econ.max_profit,
        "max_loss": econ.max_loss,
        "width": econ.width,
        "rr": econ.rr,
        "capital": econ.capital,
        "defined_risk": econ.defined_risk,
        "net_greeks": econ.net_greeks,
    }


def _default_grid(spot: float, legs: list[Leg], n: int = 41, band: float = 0.30) -> list[float]:
    """Symmetric spot grid around ``spot`` (+/- band) including in-band strikes."""
    s0 = spot if spot > 0 else 100.0
    lo, hi = s0 * (1 - band), s0 * (1 + band)
    step = (hi - lo) / (n - 1)
    grid = [lo + i * step for i in range(n)]
    for leg in legs:
        if leg.strike and lo <= leg.strike <= hi:
            grid.append(float(leg.strike))
    grid.append(float(s0))
    return sorted(set(round(x, 6) for x in grid))
