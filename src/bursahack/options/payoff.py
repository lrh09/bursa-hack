"""Terminal (expiry, T->0) payoff kernel for multi-leg option positions.

This is the single source of *terminal* payoff truth (design law L3): the expiry
P&L of any structure is the sum of each leg's signed intrinsic dollar value at the
terminal spot, minus the net entry cash. Every consumer that needs an expiry
number -- the scenario expiry ladder, the analytic probability buckets in
``prob``, the Monte-Carlo P&L vector in ``montecarlo``, and the payoff diagram in
``charts`` -- routes through ``terminal_payoff`` (scalar) or its vectorised
sibling ``terminal_payoff_array`` so there is never a parallel intrinsic formula
that can drift from the priced ladder.

Sign & unit conventions (pinned in ``instruments``)
---------------------------------------------------
  * per-leg terminal dollar value = ``leg.signed_intrinsic_value(S)`` =
    ``intrinsic(S) * leg.mult * leg.qty`` -- already signed by qty (long > 0,
    short < 0) and scaled by the per-leg multiplier.
  * ``net_cost`` is the entry debit (> 0) / credit (< 0) from
    ``position.net_cost_entry`` -- subtracted ONCE here so payoff and net-cost
    come from one source.
  * terminal P&L(S_T) = sum_legs signed_intrinsic_value(S_T) - net_cost.

Golden (bull-call 360/460 x8, net_cost_entry = 31168)
-----------------------------------------------------
  {312: -31168, 351: -31168, 390: -7168, 429: +24032, 468: +48832}
  -- locked by ``tests/options/test_scenario.py::test_expiry_ladder_x8_golden``.

References:
  - Hull, *Options, Futures, and Other Derivatives* (payoff diagrams, combinations).
  - BursaHack options BUILD CONTRACT, §7 (the golden expiry ladder).
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from bursahack.options.instruments import Leg

__all__ = [
    "terminal_payoff",
    "terminal_payoff_array",
    "expiry_ladder",
    "payoff_curve",
]


def terminal_payoff(legs: Sequence[Leg], ST: float, net_cost: float) -> float:
    """Signed terminal (expiry) dollar P&L of ``legs`` at terminal spot ``ST``.

    The T->0 single source (L3): ``sum(leg.signed_intrinsic_value(ST)) - net_cost``.
    Each leg's ``signed_intrinsic_value`` is already ``intrinsic * mult * qty``
    (signed by qty), so the sum is the gross terminal dollar value of the
    structure; subtracting ``net_cost`` (debit > 0 / credit < 0) yields P&L.

    Pass ``net_cost = 0.0`` to obtain the pure gross terminal payoff (used by the
    analytic EV integral in ``prob``).
    """
    s = float(ST)
    gross = 0.0
    for leg in legs:
        gross += leg.signed_intrinsic_value(s)
    return gross - float(net_cost)


def terminal_payoff_array(legs: Sequence[Leg], ST_array, net_cost: float) -> np.ndarray:
    """Vectorised ``terminal_payoff`` over an array of terminal spots.

    Byte-for-byte the same per-leg formula as the scalar path (intrinsic is
    closed-form per right), just evaluated on a numpy array for speed. Returns
    ``sum_legs(signed_intrinsic) - net_cost`` as a float ``ndarray`` aligned to
    ``ST_array``. Used where a consumer needs the whole curve at once (the
    Monte-Carlo P&L vector, the payoff-diagram grid).
    """
    st = np.asarray(ST_array, dtype=float)
    total = np.zeros_like(st)
    for leg in legs:
        scale = float(leg.mult) * float(leg.qty)  # signed by qty
        r = leg.right.value
        if r == "C":
            intrinsic = np.maximum(st - leg.strike, 0.0)
        elif r == "P":
            intrinsic = np.maximum(leg.strike - st, 0.0)
        elif r == "S":
            intrinsic = st  # stock: per-share terminal value is S_T
        elif r == "X":
            intrinsic = np.ones_like(st)  # cash unit
        else:  # pragma: no cover - Leg.__post_init__ guards the right
            raise ValueError(f"unknown right {leg.right!r}")
        total = total + scale * intrinsic
    return total - float(net_cost)


def expiry_ladder(legs: Sequence[Leg], S_points: Sequence[float],
                  net_cost: float) -> dict[float, float]:
    """Terminal P&L at each spot in ``S_points`` -> ``{S: pnl_total}``.

    The §7 golden ladder: a dict keyed by the (float) spot point, each value the
    signed terminal dollar P&L from ``terminal_payoff``. One valuation path, so
    this is exactly the T->0 limit of ``position.value_position`` (L3).

    GOLDEN (bull-call 360/460 x8, net_cost = 31168):
        {312: -31168, 351: -31168, 390: -7168, 429: +24032, 468: +48832}.
    """
    return {float(S): terminal_payoff(legs, float(S), net_cost) for S in S_points}


def payoff_curve(legs: Sequence[Leg], S_points: Sequence[float],
                 net_cost: float) -> dict[str, list[float]]:
    """Structured terminal-payoff curve for chart/consumer use.

    Returns ``{'S': [...], 'pnl': [...]}`` -- the spot grid and the aligned signed
    terminal P&L. Same single-source intrinsic as ``terminal_payoff``; convenient
    for the payoff diagram which wants parallel x/y arrays rather than a dict.
    """
    s_list = [float(S) for S in S_points]
    pnl = terminal_payoff_array(legs, np.asarray(s_list, dtype=float), net_cost)
    return {"S": s_list, "pnl": [float(v) for v in pnl]}
