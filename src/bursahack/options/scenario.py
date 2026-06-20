"""Scenario engine for option positions: decay ladders, spot x vol and spot x time
P&L surfaces, a long-form scenario grid, and a days-to-breakeven probe.

All P&L is single-source-of-truth (L3): every cell is the difference between the
repricing kernel `position.value_position` at the perturbed state and the realised
entry cash `position.net_cost_entry`. The terminal (expiry) row is just the T->0
limit of that same kernel, so the expiry ladder, the mark-now curve, and the T+n
curves all come from one valuation path -- never a parallel intrinsic formula.

P&L sign / units (per the shared laws):
  - long qty > 0, short qty < 0; multiplier is per-leg (leg.mult).
  - time in YEARS internally; scenario days are CALENDAR days -> T' = T - days/365.
  - dollar P&L = (position_value_per_share - net_cost_entry_per_share) summed across
    legs already scaled by leg.mult * leg.qty inside value_position / net_cost_entry.

IV handling for a scenario axis:
  - iv='hold'  -> keep each leg's effective IV (leg.iv or market.sigma) flat.
  - iv='shift' -> the spot x vol surface bumps the flat vol; callers pass shifts in
    VOL POINTS (e.g. +5 means +0.05 absolute vol) and we rebuild a MarketState with
    market.sigma + shift/100, then reprice (per-leg leg.iv still wins via _leg_iv).

References:
  - Hull, "Options, Futures, and Other Derivatives" -- scenario / stress framing.
  - Natenberg, "Option Volatility & Pricing" -- decay ladders, vol surfaces.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from bursahack.options import payoff as _payoff
from bursahack.options import position as _position
from bursahack.options import structure as _structure
from bursahack.options.types import Leg, MarketState

# ---------------------------------------------------------------------------
# Internal helpers (single valuation path)
# ---------------------------------------------------------------------------


def _resolve_legs(legs_or_position) -> list[Leg]:
    """Accept either a Position (has `.legs`) or a raw list/tuple of Leg."""
    inner = getattr(legs_or_position, "legs", None)
    if inner is not None:
        return list(inner)
    return list(legs_or_position)


def _max_T(legs: list[Leg], market: MarketState) -> float:
    """Longest leg time-to-expiry in years, measured from market.asof.

    Used only to size default spot grids / default day offsets when none are given.
    A leg with no expiry (STOCK/CASH) contributes 0.
    """
    Ts: list[float] = []
    for leg in legs:
        if leg.expiry is None or market.asof is None:
            continue
        days = (leg.expiry - market.asof.date()).days
        Ts.append(max(days, 0) / 365.0)
    return max(Ts) if Ts else 0.0


def _bump_vol(market: MarketState, shift_pts: float) -> MarketState:
    """Return a MarketState with the flat fallback vol bumped by `shift_pts` vol POINTS.

    +5 points -> +0.05 absolute. Floors the result at a tiny positive vol so the
    pricing core never sees a non-positive sigma.
    """
    new_sigma = max(market.sigma + shift_pts / 100.0, 1e-6)
    return replace(market, sigma=new_sigma)


def _pnl_at(legs: list[Leg], market: MarketState, S: float, dt_days: float,
            net_cost: float, iv_shift_pts: float = 0.0) -> float:
    """Dollar P&L of `legs` at spot S, `dt_days` calendar days elapsed, with an
    optional flat-vol bump of `iv_shift_pts` points.

    The ONE cell evaluator -- every surface / ladder / grid routes through here so
    the engine has a single source of payoff truth (L3). `net_cost` is the realised
    entry cash from `position.net_cost_entry` (debit > 0 / credit < 0).
    """
    mkt = _bump_vol(market, iv_shift_pts) if iv_shift_pts else market
    val = _position.value_position(legs, S, mkt, dt_days=dt_days)
    return float(val["position_value"]) - net_cost


def _default_spot_grid(S0: float, legs: list[Leg], market: MarketState,
                       n: int = 41, band: float = 0.30) -> list[float]:
    """Symmetric spot grid around S0 spanning +/- `band`, including all strikes so
    payoff kinks are sampled exactly."""
    lo, hi = S0 * (1 - band), S0 * (1 + band)
    grid = list(np.linspace(lo, hi, n))
    for leg in legs:
        if leg.strike and lo <= leg.strike <= hi:
            grid.append(float(leg.strike))
    grid.append(float(S0))
    return sorted(set(round(x, 6) for x in grid))


# ---------------------------------------------------------------------------
# Decay ladder
# ---------------------------------------------------------------------------


def decay_ladder(legs, market: MarketState, date_offsets: list[int] | None = None,
                 S_grid=None, iv: str = "hold") -> dict:
    """T+n P&L ladder: a P&L curve over spot for each elapsed-days offset.

    Args:
        legs: a Position or a list[Leg].
        market: the base MarketState (provides spot, r, q, sigma, asof, divs).
        date_offsets: calendar-day offsets to evaluate (default [0, 7, 30, expiry]).
        S_grid: spot grid (default symmetric band around spot incl. strikes).
        iv: 'hold' keeps vol flat across the ladder (only mode in v1).

    Returns:
        {
          'S': [...],                       # the spot grid
          'offsets': [...],                 # the day offsets actually used
          'curves': {offset: [pnl, ...]},   # dollar P&L per offset over S
          'theta_day': {offset: float},     # finite-diff $theta/day at spot per offset
          'net_cost': float,                # entry debit/credit
        }
    """
    legs = _resolve_legs(legs)
    net_cost = _position.net_cost_entry(legs)
    Tmax = _max_T(legs, market)
    expiry_days = int(round(Tmax * 365)) if Tmax > 0 else 0

    if date_offsets is None:
        base = [0, 7, 30]
        if expiry_days > 0:
            base.append(expiry_days)
        date_offsets = sorted(set(d for d in base if d <= max(expiry_days, 0) or expiry_days == 0))
    if S_grid is None:
        S_grid = _default_spot_grid(market.spot, legs, market)
    S_grid = [float(s) for s in S_grid]

    curves: dict[int, list[float]] = {}
    theta_day: dict[int, float] = {}
    for off in date_offsets:
        curves[off] = [_pnl_at(legs, market, s, float(off), net_cost) for s in S_grid]
        # $theta/day at the current spot: -(value(off+1) - value(off))
        v0 = _pnl_at(legs, market, market.spot, float(off), net_cost)
        v1 = _pnl_at(legs, market, market.spot, float(off) + 1.0, net_cost)
        theta_day[off] = float(v1 - v0)

    return {
        "S": S_grid,
        "offsets": list(date_offsets),
        "curves": curves,
        "theta_day": theta_day,
        "net_cost": net_cost,
    }


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


def surface_spot_time(legs, market: MarketState, S_range, t_range,
                      iv: str = "hold") -> dict:
    """P&L surface over spot (rows) x elapsed-days (cols).

    Args:
        S_range: iterable of spot levels (absolute).
        t_range: iterable of calendar-day offsets.
        iv: 'hold' (flat vol) in v1.

    Returns:
        {'Z', 'S_axis', 't_axis', 'max_cell', 'min_cell', 'zero_contour'}
        where Z[i][j] is dollar P&L at S_axis[i], t_axis[j]. zero_contour is the
        list of (S, t) grid cells nearest the breakeven plane (sign change in S).
    """
    legs = _resolve_legs(legs)
    net_cost = _position.net_cost_entry(legs)
    S_axis = [float(s) for s in S_range]
    t_axis = [float(t) for t in t_range]

    Z = np.empty((len(S_axis), len(t_axis)), dtype=float)
    for i, s in enumerate(S_axis):
        for j, t in enumerate(t_axis):
            Z[i, j] = _pnl_at(legs, market, s, t, net_cost)

    zero_contour: list[tuple[float, float]] = []
    for j in range(len(t_axis)):
        col = Z[:, j]
        for i in range(1, len(S_axis)):
            if col[i - 1] == 0.0 or (col[i - 1] < 0.0) != (col[i] < 0.0):
                # linear interpolate the spot crossing
                y0, y1 = col[i - 1], col[i]
                x0, x1 = S_axis[i - 1], S_axis[i]
                s_cross = x0 if y1 == y0 else x0 - y0 * (x1 - x0) / (y1 - y0)
                zero_contour.append((float(s_cross), t_axis[j]))

    idx_max = np.unravel_index(int(np.argmax(Z)), Z.shape)
    idx_min = np.unravel_index(int(np.argmin(Z)), Z.shape)
    return {
        "Z": Z,
        "S_axis": S_axis,
        "t_axis": t_axis,
        "max_cell": {"S": S_axis[idx_max[0]], "t": t_axis[idx_max[1]], "pnl": float(Z[idx_max])},
        "min_cell": {"S": S_axis[idx_min[0]], "t": t_axis[idx_min[1]], "pnl": float(Z[idx_min])},
        "zero_contour": zero_contour,
    }


def surface_spot_iv(legs, market: MarketState, S_range, iv_shifts,
                    horizon_days: float = 0) -> dict:
    """P&L surface over spot (rows) x IV-shift (cols), at a fixed elapsed horizon.

    Args:
        S_range: iterable of spot levels (absolute).
        iv_shifts: iterable of vol-point shifts (e.g. [-10,-5,0,5,10]); +5 => +0.05.
        horizon_days: calendar days elapsed for every cell (default 0 = mark now).

    Returns:
        {'Z', 'S_axis', 'iv_axis', 'max_cell', 'min_cell'} where Z[i][j] is dollar
        P&L at S_axis[i] with vol bumped iv_axis[j] points.
    """
    legs = _resolve_legs(legs)
    net_cost = _position.net_cost_entry(legs)
    S_axis = [float(s) for s in S_range]
    iv_axis = [float(v) for v in iv_shifts]

    Z = np.empty((len(S_axis), len(iv_axis)), dtype=float)
    for i, s in enumerate(S_axis):
        for j, v in enumerate(iv_axis):
            Z[i, j] = _pnl_at(legs, market, s, float(horizon_days), net_cost, iv_shift_pts=v)

    idx_max = np.unravel_index(int(np.argmax(Z)), Z.shape)
    idx_min = np.unravel_index(int(np.argmin(Z)), Z.shape)
    return {
        "Z": Z,
        "S_axis": S_axis,
        "iv_axis": iv_axis,
        "max_cell": {"S": S_axis[idx_max[0]], "iv_shift": iv_axis[idx_max[1]], "pnl": float(Z[idx_max])},
        "min_cell": {"S": S_axis[idx_min[0]], "iv_shift": iv_axis[idx_min[1]], "pnl": float(Z[idx_min])},
    }


# ---------------------------------------------------------------------------
# Scenario grid (long-form)
# ---------------------------------------------------------------------------


def _normalize_spots(spots, S0: float) -> list[float]:
    """spots may be absolute levels OR fractional moves (|x| < 1 treated as a %).

    A value with |x| < 1 (e.g. -0.10, 0.05) is read as a percentage move off S0;
    anything else is an absolute spot. 0.0 maps to S0 (no move).
    """
    out: list[float] = []
    for x in spots:
        x = float(x)
        if abs(x) < 1.0:
            out.append(S0 * (1.0 + x))
        else:
            out.append(x)
    return out


def scenario_grid(legs, market: MarketState, spots, iv_shifts, days,
                  capital: float) -> list[dict]:
    """Cartesian scenario grid -> one long-form row per (spot, iv_shift, day).

    spots accept either absolute levels or fractional moves (|x|<1 => % off spot).
    iv_shifts in vol points. days in calendar days.

    Each row:
        {'spot','iv','day','pnl','pct','delta','gamma','theta','vega'}
    where 'pct' = pnl / capital (None if capital <= 0), and the greeks are the
    position's per-share aggregate dollar greeks at that perturbed state.
    """
    legs = _resolve_legs(legs)
    net_cost = _position.net_cost_entry(legs)
    spot_levels = _normalize_spots(spots, market.spot)
    iv_levels = [float(v) for v in iv_shifts]
    day_levels = [float(d) for d in days]

    rows: list[dict] = []
    for s in spot_levels:
        for v in iv_levels:
            mkt = _bump_vol(market, v) if v else market
            for d in day_levels:
                val = _position.value_position(legs, s, mkt, dt_days=d)
                pnl = float(val["position_value"]) - net_cost
                dg = _position.dollar_greeks(legs, s, mkt, dt_days=d)
                rows.append({
                    "spot": float(s),
                    "iv": float(v),
                    "day": float(d),
                    "pnl": pnl,
                    "pct": (pnl / capital) if capital and capital > 0 else None,
                    "delta": float(dg.get("delta", 0.0)),
                    "gamma": float(dg.get("gamma", 0.0)),
                    "theta": float(dg.get("theta_day", 0.0)),
                    "vega": float(dg.get("vega", 0.0)),
                })
    return rows


# ---------------------------------------------------------------------------
# Days-to-breakeven probe
# ---------------------------------------------------------------------------


def days_to_breakeven(legs, S, market: MarketState, iv=None) -> dict:
    """How many calendar days until mark-now P&L at fixed spot S crosses zero.

    Holds spot at S and walks elapsed days forward to the longest leg expiry,
    returning the first day the position's P&L turns non-negative (theta names) or
    non-positive (decaying-long names). Useful for "if the stock sits here, when do
    I break even on theta?"

    Returns:
        {'days', 'reached', 'pnl_now', 'pnl_at_expiry', 'S'}
        'days' is None and 'reached' False if no sign change occurs before expiry.
    """
    legs = _resolve_legs(legs)
    net_cost = _position.net_cost_entry(legs)
    Tmax = _max_T(legs, market)
    horizon = int(round(Tmax * 365)) if Tmax > 0 else 0
    S = float(S)

    pnl_now = _pnl_at(legs, market, S, 0.0, net_cost)
    pnl_exp = _pnl_at(legs, market, S, float(horizon), net_cost)

    days = None
    reached = False
    if horizon > 0:
        prev = pnl_now
        for d in range(1, horizon + 1):
            cur = _pnl_at(legs, market, S, float(d), net_cost)
            if cur == 0.0 or (prev < 0.0) != (cur < 0.0):
                days = d
                reached = True
                break
            prev = cur

    return {
        "days": days,
        "reached": reached,
        "pnl_now": pnl_now,
        "pnl_at_expiry": pnl_exp,
        "S": S,
    }


# ---------------------------------------------------------------------------
# Expiry scenario convenience (mark-now / expiry economics; thin wrappers over
# payoff + structure so callers get the §7 golden ladder from one entry point)
# ---------------------------------------------------------------------------


def expiry_scenario(legs, S_points: list[float], net_cost: float | None = None) -> dict:
    """Expiry P&L at fixed spot points -- the §7 golden ladder.

    Pure intrinsic via payoff.expiry_ladder (the T->0 limit of value_position, L3).
    If net_cost is None it is taken from position.net_cost_entry (realised entry).

    GOLDEN (bull-call 360/460 x8, net_cost_entry=31168):
        {312:-31168, 351:-31168, 390:-7168, 429:+24032, 468:+48832}.
    """
    legs = _resolve_legs(legs)
    if net_cost is None:
        net_cost = _position.net_cost_entry(legs)
    return _payoff.expiry_ladder(legs, S_points, net_cost)


def economics_summary(legs, market: MarketState, s_grid: list[float]) -> dict:
    """Thin pass-through to structure.economics for callers that only have legs.

    Returns the EconSheet's dict-ified core fields (net_premium, breakevens,
    max_profit/loss, width, rr, capital, expiry_pl_ladder) so the scenario layer can
    surface breakevens / max-P-L / R-R without re-deriving them.
    """
    legs = _resolve_legs(legs)
    econ = _structure.economics(legs, market, s_grid)
    return {
        "net_premium": econ.net_premium,
        "breakevens": tuple(econ.breakevens),
        "max_profit": econ.max_profit,
        "max_loss": econ.max_loss,
        "width": econ.width,
        "rr": econ.rr,
        "capital": econ.capital,
        "expiry_pl_ladder": econ.expiry_pl_ladder,
    }
