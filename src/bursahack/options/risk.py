"""Tail-risk, defined-risk verification, and margin / buying-power lenses.

This module owns the *risk lens* over an options book: the distribution-side
metrics (VaR / CVaR, percentiles, outcome buckets, growth / Kelly) AND the
structural defined-risk / naked / margin / buying-power checks that gate whether
a position is allowed to exist at all.

Two distinct families live here, deliberately kept apart:

  1. Distribution metrics  -- consume a P&L sample array (from `mc` or an
     analytic ladder). Profit is positive, loss is negative, in account
     currency. VaR / CVaR are reported as POSITIVE losses (desk convention).

  2. Structural risk        -- consume `types` value objects (Leg / Position /
     Book) and classify defined-vs-undefined risk, scan for naked short legs,
     compute the defined-risk margin / buying-power requirement, estimate a
     portfolio-margin stress maintenance number, walk an Excess-Liquidity
     trajectory through a sequence of candidate trades, flag assignment / pin /
     leg-mismatch hazards, score expiration clustering, and report the distance
     to a margin call.

Sign / units (matches `types`): long qty > 0, short qty < 0, multiplier
per-leg (default 100), time in years. Currency is the account currency.

Defined-risk margin formulas (the gate):

    vertical credit spread :  margin = width * |qty| * mult  -  credit_received
    vertical debit  spread :  margin = debit_paid                       (= max loss)
    naked short option     :  margin = UNDEFINED  ->  flag 'unbounded'  (L7)

Portfolio-margin stress maintenance is a worst-case loss over a price x vol
stress grid. The exact full-reprice version is `margin.pm_maintenance`; the
estimate here is intrinsic / defined-risk based so the risk lens stays pure and
self-contained (no pricing-tree dependency).

Design laws honoured: L5 (sign + units pinned, per-leg mult, never hardcoded),
L7 (undefined tails are DETECTED and flagged, never silently grid-clamped).

References:
  - Jorion, "Value at Risk" (VaR / CVaR definitions, loss-as-positive sign).
  - Rockafellar & Uryasev, "Optimization of Conditional Value-at-Risk" (CVaR).
  - Kelly, "A New Interpretation of Information Rate" (log-growth / Kelly).
  - CBOE / OCC margin manual (defined-risk vertical margin = width - credit).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

from bursahack.options.types import Book, Leg, Position, Right

# ---------------------------------------------------------------------------
# Distribution-side risk metrics  (consume a P&L sample array)
# ---------------------------------------------------------------------------


def _as_pnl_array(pnl_array) -> "np.ndarray":
    """Coerce to a 1-D float ndarray; raise on empty (no risk on no sample)."""
    arr = np.asarray(pnl_array, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("pnl_array is empty -- cannot compute risk metrics")
    return arr


def var_cvar(pnl_array, alphas=(0.95, 0.99), floor: float | None = None) -> dict:
    """Value-at-Risk and Conditional VaR, reported as POSITIVE losses.

    VaR_alpha is the loss not exceeded with probability `alpha` (so VaR95 is the
    5%-worst loss threshold). CVaR_alpha is the mean loss in that worst tail.

    `floor` is the defined-risk MAX LOSS (a positive number). When given, every
    loss is clamped to it so a defined-risk position can never report a VaR
    beyond its structural max -- this is the GOLDEN (360/460 x8 -> floor=31168
    -> VaR95 == VaR99 == CVaR == 31168).

    When `floor` is None AND the empirical tail is open (the worst sampled loss
    sits at the extreme of the sample with no structural cap), `flag` is set to
    'unbounded' (L7) -- we never silently clamp an undefined tail.
    """
    arr = _as_pnl_array(pnl_array)
    losses = -arr  # loss positive
    if floor is not None:
        losses = np.minimum(losses, float(floor))

    out: dict = {}
    var_by_alpha: dict[float, float] = {}
    cvar_by_alpha: dict[float, float] = {}
    for a in alphas:
        # VaR_alpha = alpha-quantile of the loss distribution.
        var_a = float(np.quantile(losses, a))
        tail = losses[losses >= var_a]
        cvar_a = float(tail.mean()) if tail.size else var_a
        # CVaR is a mean of the worst tail -> never less than VaR.
        cvar_a = max(cvar_a, var_a)
        var_by_alpha[a] = var_a
        cvar_by_alpha[a] = cvar_a
        out[f"var_{int(round(a * 100))}"] = var_a
        out[f"cvar_{int(round(a * 100))}"] = cvar_a

    worst_loss = float(losses.max())
    out["var"] = var_by_alpha
    out["cvar"] = cvar_by_alpha
    out["worst_loss"] = worst_loss
    out["floor"] = float(floor) if floor is not None else None
    out["mean_pnl"] = float(arr.mean())
    out["n"] = int(arr.size)

    if floor is not None:
        out["flag"] = "floored"
    else:
        # No structural cap -> the loss tail is open: flag it (L7).
        out["flag"] = "unbounded"
    return out


def pnl_percentiles(pnl_array, qs=(5, 10, 25, 50, 75, 90, 95)) -> dict:
    """P&L distribution percentiles (signed P&L, profit positive).

    Returns {'p<q>': value, 'percentiles': {q: value}, plus mean/std/min/max}.
    """
    arr = _as_pnl_array(pnl_array)
    pct: dict[float, float] = {}
    out: dict = {}
    for q in qs:
        v = float(np.percentile(arr, q))
        pct[q] = v
        out[f"p{int(round(q))}"] = v
    out["percentiles"] = pct
    out["mean"] = float(arr.mean())
    out["std"] = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    out["min"] = float(arr.min())
    out["max"] = float(arr.max())
    out["n"] = int(arr.size)
    return out


def outcome_buckets(pnl_array, buckets: list | None = None,
                    st_thresholds: list | None = None) -> dict:
    """Categorical breakdown of terminal P&L outcomes.

    `buckets` is an ascending list of P&L cut-points; the array is binned into
    the half-open intervals they define (with -inf / +inf tails). Default
    buckets split big-loss / loss / scratch / profit / big-profit around 0.

    `st_thresholds` is accepted for signature parity with the contract (caller
    may want S_T-keyed buckets); when None it is ignored. When provided it is
    echoed back under 'st_thresholds' for the caller to map.
    """
    arr = _as_pnl_array(pnl_array)
    n = arr.size

    win = float((arr > 0).mean())
    lose = float((arr < 0).mean())
    scratch = float((arr == 0).mean())

    if buckets is None:
        spread = max(abs(float(arr.min())), abs(float(arr.max())), 1.0)
        half = 0.5 * spread
        edges = [-spread, -half, 0.0, half, spread]
    else:
        edges = sorted(float(b) for b in buckets)

    full_edges = [-math.inf, *edges, math.inf]
    counts: list[dict] = []
    for lo, hi in zip(full_edges[:-1], full_edges[1:]):
        mask = (arr > lo) & (arr <= hi) if lo != -math.inf else (arr <= hi)
        if hi == math.inf:
            mask = arr > lo
        c = int(mask.sum())
        counts.append({
            "lo": lo, "hi": hi,
            "count": c, "prob": (c / n) if n else 0.0,
        })

    out: dict = {
        "prob_win": win,
        "prob_lose": lose,
        "prob_scratch": scratch,
        "buckets": counts,
        "n": int(n),
    }
    if st_thresholds is not None:
        out["st_thresholds"] = [float(t) for t in st_thresholds]
    return out


def growth_metrics(pnl_array, capital: float, ruin_threshold: float,
                   kelly_cap: float = 0.5) -> dict:
    """Log-growth / Kelly / ruin metrics for a repeated-bet framing.

    The Kelly fraction is HARD-CAPPED at `kelly_cap` -- this function NEVER
    returns a full Kelly above the cap (a deliberate guardrail against the
    over-bet failure mode). `half_kelly` is half the (capped) fraction.

    - e_log_growth : E[ln(1 + pnl/capital)] over outcomes where 1+pnl/cap > 0;
                     ruinous outcomes (terminal equity <= 0) are treated as ln
                     of a tiny epsilon so they dominate (log-utility punishes
                     ruin to -inf, floored to keep the mean finite).
    - kelly_fraction : mean / variance estimate of edge, clipped to [0, cap].
    - geo_mean : geometric mean growth multiple per bet.
    - prob_ruin : P(terminal equity <= ruin_threshold).
    - cvar_constraint : CVaR95 as a positive loss (the tail the size must respect).
    """
    arr = _as_pnl_array(pnl_array)
    if capital <= 0:
        raise ValueError("capital must be positive")

    rel = arr / capital
    growth_mult = 1.0 + rel
    # Floor the multiple to a tiny epsilon so ln stays finite on ruinous draws.
    eps = 1e-12
    safe_mult = np.clip(growth_mult, eps, None)
    log_growth = np.log(safe_mult)
    e_log_growth = float(log_growth.mean())
    geo_mean = float(np.exp(e_log_growth))

    mean_rel = float(rel.mean())
    var_rel = float(rel.var(ddof=1)) if arr.size > 1 else 0.0
    # Continuous-Kelly approximation f* = mean / variance of return-on-capital.
    raw_kelly = (mean_rel / var_rel) if var_rel > 0 else 0.0
    kelly_fraction = float(min(max(raw_kelly, 0.0), kelly_cap))
    half_kelly = 0.5 * kelly_fraction

    prob_ruin = float((arr <= ruin_threshold).mean())

    tail_loss = -arr
    var95 = float(np.quantile(tail_loss, 0.95))
    tail = tail_loss[tail_loss >= var95]
    cvar95 = float(tail.mean()) if tail.size else var95

    return {
        "e_log_growth": e_log_growth,
        "kelly_fraction": kelly_fraction,
        "raw_kelly": float(raw_kelly),
        "half_kelly": half_kelly,
        "kelly_cap": float(kelly_cap),
        "geo_mean": geo_mean,
        "prob_ruin": prob_ruin,
        "cvar_constraint": cvar95,
        "mean_pnl": float(arr.mean()),
        "n": int(arr.size),
    }


# ---------------------------------------------------------------------------
# Structural risk : defined-risk verification + naked detection + margin / BP
# ---------------------------------------------------------------------------
#
# These consume `types` value objects. The defined-risk gate (L7) is decided by
# an asymptotic-slope test on the terminal payoff: the per-share payoff slope as
# S -> 0 (left tail) and S -> +inf (right tail). A non-zero outward slope on a
# tail means the position bleeds without bound there -> UNDEFINED risk.


def _option_legs(legs) -> list[Leg]:
    return [lg for lg in legs if lg.right in (Right.CALL, Right.PUT)]


def _intrinsic_per_share(leg: Leg, S: float) -> float:
    """Signed per-share intrinsic value of one leg at spot S (sign from qty)."""
    if leg.right is Right.CALL:
        intr = max(S - leg.strike, 0.0)
    elif leg.right is Right.PUT:
        intr = max(leg.strike - S, 0.0)
    elif leg.right is Right.STOCK:
        intr = S
    else:  # CASH
        intr = 1.0
    # signed by qty direction; magnitude carries qty so caller can sum directly
    return math.copysign(1.0, leg.qty) * abs(leg.qty) * intr * leg.mult


def _payoff_total(legs, S: float) -> float:
    """Gross terminal payoff of the leg set at S (no net cost)."""
    return sum(_intrinsic_per_share(lg, S) for lg in legs)


def _tail_slopes(legs, S_ref: float) -> tuple[float, float]:
    """(left_slope, right_slope) of the gross payoff in the far tails.

    left_slope  = d(payoff)/dS evaluated as S -> 0   (negative S region proxy)
    right_slope = d(payoff)/dS evaluated as S -> +inf
    A non-zero outward slope => that tail is open (undefined risk).
    """
    base = max(S_ref, 1.0)
    far_hi = base * 1000.0
    far_hi2 = base * 1001.0
    right_slope = (_payoff_total(legs, far_hi2) - _payoff_total(legs, far_hi)) / (far_hi2 - far_hi)
    # left tail: between a small positive S and ~0
    lo1, lo2 = 1e-6, base * 1e-3
    left_slope = (_payoff_total(legs, lo2) - _payoff_total(legs, lo1)) / (lo2 - lo1)
    return left_slope, right_slope


@dataclass(frozen=True)
class DefinedRiskReport:
    defined_risk: bool
    max_loss: float | str          # positive loss magnitude, or 'unbounded'
    max_profit: float | str
    unbounded_side: str | None     # 'up' | 'down' | 'both' | None
    naked_legs: tuple[Leg, ...]
    breakevens: tuple[float, ...]


def net_cost_entry(legs) -> float:
    """Signed net cash at FILL (debit > 0, credit < 0).

    Mirrors `position.net_cost_entry` so the risk lens can compute max-loss /
    max-profit standalone (pure, market-independent). A bought leg (qty > 0)
    pays its premium (debit); a sold leg (qty < 0) collects it (credit).
    """
    total = 0.0
    for lg in legs:
        if lg.right in (Right.STOCK, Right.CASH):
            continue
        # debit for longs (+), credit for shorts (-)
        total += math.copysign(1.0, lg.qty) * abs(lg.qty) * lg.entry_price * lg.mult
    return total


def _breakevens(legs, net_cost: float) -> tuple[float, ...]:
    """Numeric breakevens of net P&L over the kink grid."""
    opt = _option_legs(legs)
    if not opt:
        return ()
    strikes = sorted({lg.strike for lg in opt})
    lo = max(strikes[0] * 0.5, 0.0)
    hi = strikes[-1] * 1.5 + 1.0
    grid = sorted({lo, hi, *strikes, *[k * 0.999 for k in strikes], *[k * 1.001 for k in strikes]})
    bes: list[float] = []

    def pnl(S: float) -> float:
        return _payoff_total(legs, S) - net_cost

    prev_S = grid[0]
    prev_v = pnl(prev_S)
    for S in grid[1:]:
        v = pnl(S)
        if prev_v * v < 0:
            # linear interpolate the zero crossing (payoff is piecewise linear)
            be = prev_S - prev_v * (S - prev_S) / (v - prev_v)
            bes.append(round(be, 6))
        elif prev_v == 0.0 and v != 0.0:
            # leaving a flat-on-zero plateau into a non-zero region: the plateau
            # EDGE is the genuine breakeven. With net_cost==0 (entry=0 books) the
            # OTM region of an option sits flat ON zero over a whole range — that
            # is ONE degenerate breakeven (the strike), not a breakeven per grid
            # point. Recording only the edge avoids the spurious 110.00/219.78-style
            # plateau artifacts while keeping the true strike crossing.
            bes.append(round(prev_S, 6))
        elif prev_v != 0.0 and v == 0.0:
            # entering a flat-on-zero plateau from a non-zero region.
            bes.append(round(S, 6))
        prev_S, prev_v = S, v
    # de-dup
    uniq: list[float] = []
    for b in bes:
        if not any(math.isclose(b, u, abs_tol=1e-6) for u in uniq):
            uniq.append(b)
    return tuple(uniq)


def defined_risk(legs, slope_tol: float = 1e-6) -> DefinedRiskReport:
    """Verify whether a leg set has DEFINED (bounded) risk.

    Asymptotic-slope test on the gross terminal payoff (L7). The KEY asymmetry
    for equity options: the down side ALWAYS terminates at S=0 (calls -> 0, puts
    -> their strike, stock -> 0), so the worst down-case is the FINITE payoff at
    S=0. The up side, S -> +inf, is the only genuinely open direction -- a net
    negative right-tail slope (uncovered short calls / short stock) means losses
    grow without bound.  So:

      undefined risk  <=>  right_slope < -tol   (payoff -> -inf as S rises)

    A net negative LEFT-tail slope (payoff rising as S rises near 0 -> falling as
    S falls -> short puts / long stock) is a LARGE but BOUNDED loss, realised at
    S=0; we record it as `unbounded_side='down'` ONLY when the structure is also
    up-unbounded (i.e. a synthetic-short / short straddle style 'both'). A pure
    short put / covered call reports defined_risk=True with max_loss at S=0.

    Naked legs are the short calls on the open up-tail. max_loss / max_profit
    come from the kinked payoff (kinks + S=0 + a far-up point) net of entry cost.
    """
    opt = _option_legs(legs)
    has_stock = any(lg.right is Right.STOCK for lg in legs)
    if not opt and not has_stock:
        return DefinedRiskReport(
            defined_risk=True, max_loss=0.0, max_profit=0.0,
            unbounded_side=None, naked_legs=(), breakevens=(),
        )

    strikes = sorted({lg.strike for lg in opt}) or [0.0]
    S_ref = strikes[len(strikes) // 2] if strikes else 1.0
    left_slope, right_slope = _tail_slopes(legs, max(S_ref, 1.0))

    right_open = right_slope < -slope_tol   # payoff -> -inf as S rises (TRUE unbounded LOSS)
    up_profit_open = right_slope > slope_tol  # payoff -> +inf as S rises (UNBOUNDED PROFIT)
    left_down = left_slope > slope_tol      # payoff falls as S falls (bounded at S=0)

    net_cost = net_cost_entry(legs)

    # candidate extrema occur at kinks (strikes) + S=0 + a far-up point
    far_up = (strikes[-1] if strikes else max(S_ref, 1.0)) * 3.0 + 1.0
    eval_pts = [0.0, *strikes, far_up]
    pnls = [(_payoff_total(legs, S) - net_cost) for S in eval_pts]

    # naked legs: short calls on the open up-tail
    naked: list[Leg] = (
        [lg for lg in opt if lg.qty < 0 and lg.right is Right.CALL]
        if right_open else []
    )

    if right_open and left_down:
        side: str | None = "both"
    elif right_open:
        side = "up"
    else:
        side = None  # down-only large loss is still DEFINED (caps at S=0)

    defined = not right_open
    if defined:
        max_loss: float | str = float(-min(pnls))      # positive loss, from S=0/kinks
        # An open right-tail with POSITIVE slope (net long calls into the far tail)
        # has genuinely UNBOUNDED upside — the payoff at K×3+1 is a grid artifact,
        # not a real ceiling and not comparable across strikes. Report it as such
        # (symmetric to the unbounded-LOSS handling). A right-FLAT tail (capped
        # structures: spreads / condors) keeps its finite, correct max_profit.
        max_profit: float | str = "unbounded" if up_profit_open else float(max(pnls))
    else:
        max_loss = "unbounded"
        max_profit = float(max(pnls))                  # upside profit still capped if no long call net

    return DefinedRiskReport(
        defined_risk=defined,
        max_loss=max_loss,
        max_profit=max_profit,
        unbounded_side=side,
        naked_legs=tuple(naked),
        breakevens=_breakevens(legs, net_cost),
    )


def naked_scan(legs) -> dict:
    """Position-level naked-short scan.

    A short option is COVERED when its loss tail is capped:
      - short CALL : a long CALL further OTM (higher strike) OR long stock
        (qty >= the short call's share-equivalent) caps the up-tail.
      - short PUT  : a long PUT further OTM (lower strike) caps the down-tail
        (the down-tail is finite regardless, but a long put still defines it).
    Long-qty must be >= short-qty for full coverage (else partially covered).

    Returns {has_naked, naked_legs, covered_legs, unbounded_side, defined_risk,
    note}. The authoritative WHOLE-BOOK naked truth (cross-position hedges) is
    `book.naked_scan` -- this is the single-position lens.
    """
    rep = defined_risk(legs)
    opt = _option_legs(legs)
    shorts = [lg for lg in opt if lg.qty < 0]
    longs = [lg for lg in opt if lg.qty > 0]
    long_call_strikes = sorted(lg.strike for lg in longs if lg.right is Right.CALL)
    long_put_strikes = sorted((lg.strike for lg in longs if lg.right is Right.PUT), reverse=True)
    long_shares = sum(lg.qty * lg.mult for lg in legs if lg.right is Right.STOCK)

    naked: list[Leg] = []
    covered: list[Leg] = []
    for sh in shorts:
        short_share_equiv = abs(sh.qty) * sh.mult
        if sh.right is Right.CALL:
            has_long_call = any(k > sh.strike for k in long_call_strikes)
            stock_covered = long_shares >= short_share_equiv
            is_covered = has_long_call or stock_covered
        else:  # PUT
            is_covered = any(k < sh.strike for k in long_put_strikes)
        (covered if is_covered else naked).append(sh)

    return {
        "has_naked": bool(naked),
        "naked_legs": tuple(naked),
        "covered_legs": tuple(covered),
        "unbounded_side": rep.unbounded_side,
        "defined_risk": rep.defined_risk,
        "note": (
            "Single-position scan. Whole-book naked truth (cross-position "
            "hedges) is book.naked_scan."
        ),
    }


# ---------------------------------------------------------------------------
# Margin / buying-power  (defined-risk requirement + PM stress estimate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarginEstimate:
    requirement: float            # buying-power / maintenance requirement
    method: str                   # 'defined_risk_vertical' | 'debit' | 'pm_stress' | 'naked_undefined'
    defined_risk: bool
    width: float | None
    credit: float                 # net credit collected (>=0) or 0
    debit: float                  # net debit paid (>=0) or 0
    flag: str                     # '' | 'unbounded'
    detail: str


def _vertical_width(legs) -> float | None:
    """If the leg set is a single same-right vertical, return its strike width."""
    opt = _option_legs(legs)
    if len(opt) != 2:
        return None
    a, b = opt
    if a.right is not b.right:
        return None
    if abs(a.qty) != abs(b.qty):
        return None
    if (a.qty > 0) == (b.qty > 0):
        return None  # must be one long one short
    return abs(a.strike - b.strike)


def defined_risk_margin(legs, mult: int = 100) -> MarginEstimate:
    """Buying-power requirement for a defined-risk structure.

    Credit vertical :  margin = width * |qty| * mult  -  credit_received
    Debit  vertical :  margin = debit_paid                 (= max loss, already paid)
    Naked short     :  flag 'unbounded' (L7) -- no defined BP; routes to PM stress.

    GOLDEN (bull-call 360/460 x8): debit spread, debit = 38.96 * 8 * 100 = 31168
    -> requirement 31168, method 'debit'. (TASK formula width*qty*100 - credit
    is the CREDIT-spread case; e.g. 500/530 x1 credit -> 30*1*100 - credit.)
    """
    rep = defined_risk(legs)
    net = net_cost_entry(legs)            # debit > 0, credit < 0
    width = _vertical_width(legs)
    credit = -net if net < 0 else 0.0
    debit = net if net > 0 else 0.0

    if not rep.defined_risk:
        return MarginEstimate(
            requirement=float("inf"),
            method="naked_undefined",
            defined_risk=False,
            width=width,
            credit=credit,
            debit=debit,
            flag="unbounded",
            detail="Undefined-risk tail -- no defined BP; use pm_stress_maintenance.",
        )

    if width is not None and credit > 0:
        # canonical credit vertical
        opt = _option_legs(legs)
        qty = abs(opt[0].qty)
        req = width * qty * mult - credit
        return MarginEstimate(
            requirement=float(max(req, 0.0)),
            method="defined_risk_vertical",
            defined_risk=True,
            width=width,
            credit=credit,
            debit=debit,
            flag="",
            detail=f"Credit vertical: width {width} * qty {qty} * {mult} - credit {credit:.2f}.",
        )

    if debit > 0:
        # debit spread / long premium: requirement is the debit paid (= max loss)
        return MarginEstimate(
            requirement=float(debit),
            method="debit",
            defined_risk=True,
            width=width,
            credit=credit,
            debit=debit,
            flag="",
            detail=f"Debit structure: requirement = debit paid {debit:.2f} (= max loss).",
        )

    # defined-risk but neither a clean credit vertical nor a net debit
    # (e.g. iron condor): max loss is the BP requirement.
    max_loss = rep.max_loss if isinstance(rep.max_loss, (int, float)) else 0.0
    return MarginEstimate(
        requirement=float(max_loss),
        method="defined_risk_vertical",
        defined_risk=True,
        width=width,
        credit=credit,
        debit=debit,
        flag="",
        detail="Defined-risk structure: requirement = structural max loss.",
    )


@dataclass(frozen=True)
class StressPolicy:
    spot_band: float = 0.15        # +/- 15% price stress
    spot_steps: int = 7
    vol_pts: tuple = (-10.0, -5.0, 0.0, 5.0, 10.0)  # informational; intrinsic est ignores vol


def pm_stress_maintenance(legs, S: float, mult: int = 100,
                          policy: StressPolicy = StressPolicy()) -> dict:
    """Portfolio-margin maintenance ESTIMATE: worst-case loss over a price band.

    This is the risk-lens INTRINSIC estimate -- it stresses spot across a +/-
    band and takes the worst terminal-P&L loss. The exact full-reprice version
    (which also stresses vol via the pricing tree) is `margin.pm_maintenance`;
    this estimate is deliberately pure (no pricing dependency) and is a lower
    bound on the true maintenance for long-premium structures.

    Maintenance is the worst-case DECLINE in the position's (intrinsic) value
    across the band, i.e. max(0, value(S) - min_over_band value(stressed)). The
    base-spot value cancels the net entry cost, so adding a leg that NETS against
    existing risk contributes ~0 -- the PMCC GOLDEN.

    For a deep-ITM long leg the stressed decline ~ delta * 100 * shock (the leg
    behaves like stock in the band, delta ~ 1) -- the GOLDEN behaviour.

    Returns {maintenance, binding_spot, base_value, worst_value, defined_risk,
    flag, grid}.
    """
    rep = defined_risk(legs)
    band = policy.spot_band
    steps = max(policy.spot_steps, 2)
    spots = [S * (1.0 + band * (2.0 * i / (steps - 1) - 1.0)) for i in range(steps)]
    spots = [s for s in spots if s > 0]

    base_value = _payoff_total(legs, S)        # intrinsic value at base spot
    grid: list[dict] = []
    worst_value = math.inf
    binding = S
    for s in spots:
        v = _payoff_total(legs, s)
        decline = base_value - v               # positive = loss
        grid.append({"spot": round(s, 4), "value": round(v, 4),
                     "decline": round(decline, 4)})
        if v < worst_value:
            worst_value = v
            binding = s

    maintenance = max(0.0, base_value - worst_value)
    flag = "unbounded" if not rep.defined_risk else ""

    return {
        "maintenance": float(maintenance),
        "binding_spot": round(binding, 4),
        "base_value": round(base_value, 4),
        "worst_value": round(worst_value, 4),
        "defined_risk": rep.defined_risk,
        "flag": flag,
        "grid": grid,
    }


def pm_reval_maintenance(legs, market, mult: int = 100,
                         policy: StressPolicy = StressPolicy()) -> dict:
    """Portfolio-margin maintenance via FULL REPRICING over a spot x vol grid.

    Unlike :func:`pm_stress_maintenance` (intrinsic / terminal-payoff only, no
    time value, no vol), this reprices every leg through the L3 valuation kernel
    (``position.value_position``) at each node of a price band x vol-shift grid
    and takes the worst-case DECLINE of the MARK value from the base node.

    Why this is the default for absolute numbers: the intrinsic estimate ignores
    extrinsic (time) value and vol, so for a long-premium book it is a *lower
    bound* on the true maintenance — it understates how much MARK value a
    long-premium structure loses under a down-and-vol-crush stress. Full reval
    captures the extrinsic bleed and the vega hit, so its worst-case decline is
    >= the intrinsic estimate for long-premium structures (locked by the test).

    The spot band and step count come from ``policy.spot_band`` / ``spot_steps``
    (same as the intrinsic fn). ``policy.vol_pts`` are vol *points* (percentage
    points) ADDED to each leg's effective vol via the flat ``iv`` override on the
    kernel — e.g. sigma 0.46 with vol_pt -10 reprices at 0.36, +10 at 0.56. The
    base node (0% spot shock, 0 vol shift) is the reference MARK; maintenance is
    ``max(0, base_mark - min_over_grid stressed_mark)``.

    Maintenance is sign-correct for a long-premium book (positive MARK, declines
    under stress) AND for credit structures (the worst node is the largest mark
    drop = largest loss). Netting still holds: combining hedged legs into one
    leg-set lets the long/short marks offset, so the combined worst-case decline
    is <= the naive sum of per-leg worst-case declines (the PMCC/hedge GOLDEN).

    ``market`` is a ``MarketState`` (carries r / q / sigma / asof / div_schedule)
    — full repricing needs the rate/carry/vol surface the intrinsic estimate can
    ignore. ``value_position`` is imported lazily to keep the risk lens free of a
    module-load-time dependency on the pricing kernel.

    Returns the SAME keys as :func:`pm_stress_maintenance`:
    {maintenance, binding_spot, base_value, worst_value, defined_risk, flag,
    grid}. ``grid`` rows carry the vol shift too (``vol_pt``) so a reader can see
    which (spot, vol) node binds. ``method`` is added ('reval') for transparency.
    """
    # Lazy import: avoids a load-time cycle (report/decision import both risk and
    # position; position imports the pricing cores). At call time the graph is set.
    from bursahack.options.position import value_position

    rep = defined_risk(legs)
    base_spot = float(getattr(market, "spot", 0.0) or 0.0)
    if base_spot <= 0:
        raise ValueError("pm_reval_maintenance requires a positive market.spot")

    base_sigma = float(getattr(market, "sigma", 0.0) or 0.0)
    band = policy.spot_band
    steps = max(policy.spot_steps, 2)
    spots = [base_spot * (1.0 + band * (2.0 * i / (steps - 1) - 1.0))
             for i in range(steps)]
    spots = [s for s in spots if s > 0]

    # Vol shifts in points -> fractional vol overrides. A 0-point shift with a
    # zero base sigma means "use each leg's own iv / market sigma" -> iv=None so
    # the kernel's per-leg vol resolution is honoured at the base node.
    vol_pts = tuple(policy.vol_pts) if policy.vol_pts else (0.0,)

    def _mark(S: float, vol_pt: float) -> float:
        # vol override: base sigma shifted by vol_pt/100; None when there is no
        # meaningful base sigma AND no shift (let each leg resolve its own vol).
        if base_sigma > 0:
            iv = max(base_sigma + float(vol_pt) / 100.0, 1e-6)
        elif float(vol_pt) != 0.0:
            iv = max(float(vol_pt) / 100.0, 1e-6)
        else:
            iv = None
        return float(value_position(legs, S, market, dt_days=0.0, iv=iv)["position_value"])

    # Base node: 0% spot shock, 0 vol shift (the reference MARK).
    base_value = _mark(base_spot, 0.0)

    grid: list[dict] = []
    worst_value = math.inf
    binding_spot = base_spot
    binding_vol_pt = 0.0
    for s in spots:
        for vp in vol_pts:
            v = _mark(s, vp)
            decline = base_value - v               # positive = loss of mark
            grid.append({
                "spot": round(s, 4), "vol_pt": float(vp),
                "value": round(v, 4), "decline": round(decline, 4),
            })
            if v < worst_value:
                worst_value = v
                binding_spot = s
                binding_vol_pt = float(vp)

    maintenance = max(0.0, base_value - worst_value)
    flag = "unbounded" if not rep.defined_risk else ""

    return {
        "maintenance": float(maintenance),
        "binding_spot": round(binding_spot, 4),
        "binding_vol_pt": binding_vol_pt,
        "base_value": round(base_value, 4),
        "worst_value": round(worst_value, 4),
        "defined_risk": rep.defined_risk,
        "flag": flag,
        "method": "reval",
        "grid": grid,
    }


def bp_impact(candidate_legs, existing_legs=None, S: float | None = None,
              mult: int = 100, policy: StressPolicy = StressPolicy()) -> dict:
    """Incremental buying-power impact of ADDING `candidate_legs` to a book.

    BP_impact = BP(existing + candidate) - BP(existing). For defined-risk
    additions this is the candidate's own requirement; for additions that NET
    against existing risk (e.g. a PMCC short call sold against an existing
    deep-ITM long call) the incremental BP is ~0 -- the GOLDEN.

    When `S` is given the comparison uses the PM stress estimate (captures
    netting); otherwise it falls back to the defined-risk margin sum.

    Returns {bp_impact, bp_before, bp_after, method}.
    """
    existing = list(existing_legs or [])
    combined = existing + list(candidate_legs)

    if S is not None:
        before = pm_stress_maintenance(existing, S, mult, policy)["maintenance"] if existing else 0.0
        after = pm_stress_maintenance(combined, S, mult, policy)["maintenance"]
        method = "pm_stress"
    else:
        before = defined_risk_margin(existing, mult).requirement if existing else 0.0
        after = defined_risk_margin(combined, mult).requirement
        method = "defined_risk"

    # guard inf arithmetic
    if math.isinf(before) or math.isinf(after):
        impact = float("inf")
    else:
        impact = after - before

    return {
        "bp_impact": impact,
        "bp_before": before,
        "bp_after": after,
        "method": method,
    }


# ---------------------------------------------------------------------------
# Excess-Liquidity trajectory + margin-call distance
# ---------------------------------------------------------------------------


def excess_liquidity_path(initial_netliq: float, base_maintenance: float,
                          trade_bp_impacts: list[float]) -> dict:
    """Walk Excess-Liquidity (= netliq - maintenance) through a trade sequence.

    Each element of `trade_bp_impacts` is the incremental maintenance a trade
    adds (use `bp_impact(...)['bp_impact']`). Returns the running EL after each
    step plus the first step (if any) that drives EL negative (a margin call).

    Returns {steps:[{idx, bp_impact, maintenance, excess_liquidity}],
             final_excess_liquidity, first_breach_idx}.
    """
    maintenance = base_maintenance
    el = initial_netliq - maintenance
    steps: list[dict] = [{
        "idx": -1, "bp_impact": 0.0,
        "maintenance": maintenance, "excess_liquidity": el,
    }]
    first_breach: int | None = None
    for i, dbp in enumerate(trade_bp_impacts):
        maintenance += dbp
        el = initial_netliq - maintenance
        steps.append({
            "idx": i, "bp_impact": dbp,
            "maintenance": maintenance, "excess_liquidity": el,
        })
        if el < 0 and first_breach is None:
            first_breach = i
    return {
        "steps": steps,
        "final_excess_liquidity": initial_netliq - maintenance,
        "final_maintenance": maintenance,
        "first_breach_idx": first_breach,
    }


def margin_call_distance(netliq: float, maintenance: float) -> dict:
    """Distance to a margin call: how far Excess-Liquidity can fall before zero.

    Returns {excess_liquidity, cushion_pct, distance_to_call, in_call}.
    cushion_pct is EL as a fraction of netliq.
    """
    el = netliq - maintenance
    cushion_pct = (el / netliq) if netliq > 0 else 0.0
    return {
        "excess_liquidity": el,
        "cushion_pct": cushion_pct,
        "distance_to_call": el,          # EL is exactly the buffer to the call
        "in_call": el < 0,
        "maintenance": maintenance,
        "netliq": netliq,
    }


# ---------------------------------------------------------------------------
# Assignment / pin / leg-mismatch scan + expiration-cluster risk
# ---------------------------------------------------------------------------


def assignment_pin_scan(legs, S: float, dividends: dict | None = None,
                        pin_band: float = 0.01) -> dict:
    """Scan short legs for assignment / pin / leg-mismatch hazards.

    - assignment_risk : short ITM option near/through its strike. For a short
      CALL, extrinsic < pending dividend (`dividends[symbol]`) escalates to
      early-assignment risk (the dividend-capture motive).
    - pin_risk        : spot within `pin_band` of a short strike (the leg may
      or may not be assigned -> Monday gap exposure).
    - leg_mismatch    : a spread whose legs have different expiries (calendar /
      diagonal) so one leg can be assigned/expire while the other lives on.

    Returns {assignment_flags:[...], pin_flags:[...], leg_mismatch:bool, notes}.
    """
    dividends = dividends or {}
    opt = _option_legs(legs)
    shorts = [lg for lg in opt if lg.qty < 0]

    assignment_flags: list[dict] = []
    pin_flags: list[dict] = []
    for lg in shorts:
        itm = (lg.right is Right.CALL and S > lg.strike) or \
              (lg.right is Right.PUT and S < lg.strike)
        intrinsic = (max(S - lg.strike, 0.0) if lg.right is Right.CALL
                     else max(lg.strike - S, 0.0))
        div = float(dividends.get(lg.underlying, 0.0))
        # extrinsic = entry premium - intrinsic (rough; entry as a mark proxy)
        extrinsic = max(lg.entry_price - intrinsic, 0.0)
        early_div = (lg.right is Right.CALL and div > 0 and extrinsic < div)
        if itm:
            assignment_flags.append({
                "leg": lg,
                "right": lg.right.value,
                "strike": lg.strike,
                "intrinsic": round(intrinsic, 4),
                "extrinsic": round(extrinsic, 4),
                "early_div_risk": early_div,
                "dividend": div,
            })
        if abs(S - lg.strike) <= pin_band * max(lg.strike, 1.0):
            pin_flags.append({
                "leg": lg, "strike": lg.strike,
                "distance_pct": abs(S - lg.strike) / max(lg.strike, 1.0),
            })

    expiries = {lg.expiry for lg in opt if lg.expiry is not None}
    leg_mismatch = len(expiries) > 1

    return {
        "assignment_flags": assignment_flags,
        "pin_flags": pin_flags,
        "leg_mismatch": leg_mismatch,
        "n_short_options": len(shorts),
        "notes": (
            "Leg-mismatch true => calendar/diagonal: one leg can be "
            "assigned/expire while the other lives on (single-leg risk)."
            if leg_mismatch else ""
        ),
    }


def expiration_cluster_risk(book: Book, cluster_window_days: int = 7) -> dict:
    """Score how concentrated a book's expiries are (gamma/pin cluster risk).

    Groups every option leg's expiry into buckets `cluster_window_days` wide and
    counts contracts per bucket. A heavy single bucket means a lot of P&L
    resolves on one day (concentrated pin / assignment / gamma risk).

    Returns {by_expiry:{iso_date: contracts}, clusters:[...], max_cluster,
             concentration}.
    """
    by_expiry: dict[str, float] = {}
    for pos in book.positions:
        for lg in pos.legs:
            if lg.right in (Right.CALL, Right.PUT) and lg.expiry is not None:
                key = lg.expiry.isoformat()
                by_expiry[key] = by_expiry.get(key, 0.0) + abs(lg.qty)

    total = sum(by_expiry.values())
    # cluster adjacent expiries within the window
    dates = sorted(date.fromisoformat(k) for k in by_expiry)
    clusters: list[dict] = []
    cur: list[date] = []
    for d in dates:
        if not cur or (d - cur[-1]).days <= cluster_window_days:
            cur.append(d)
        else:
            clusters.append(cur)
            cur = [d]
    if cur:
        clusters.append(cur)

    cluster_rows = []
    max_cluster = 0.0
    for cl in clusters:
        contracts = sum(by_expiry[d.isoformat()] for d in cl)
        cluster_rows.append({
            "start": cl[0].isoformat(),
            "end": cl[-1].isoformat(),
            "contracts": contracts,
            "expiries": [d.isoformat() for d in cl],
        })
        max_cluster = max(max_cluster, contracts)

    concentration = (max_cluster / total) if total > 0 else 0.0
    return {
        "by_expiry": by_expiry,
        "clusters": cluster_rows,
        "max_cluster": max_cluster,
        "total_contracts": total,
        "concentration": concentration,
    }
