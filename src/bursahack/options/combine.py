"""Subset selection + combination layer over a Book (the decision layer's base).

This is the *combination* half of the decision/combination layer that sits ON
TOP of the GREEN ``bursahack.options`` core. It owns nothing that the core
already owns -- it composes the existing primitives:

  * position identity / stable ids  ->  :func:`position_index` / :class:`PositionRef`
  * 1-based subset selection         ->  :func:`select_subset` / :func:`subset_to_legs`
  * price-move impact sweep          ->  :func:`subset_impact`
  * liquidation point (EL -> 0)      ->  :func:`liquidation_point`
  * structured comparison table      ->  :func:`compare_subsets`
  * netting-benefit maintenance      ->  :func:`combined_maintenance`

Single source of maintenance (decisive ruling 1)
------------------------------------------------
Every maintenance number routes through ONE of two core primitives, never a new
formula:

  * ``risk.pm_stress_maintenance(legs, S)``  -- per leg-set stress estimate.
  * ``margin.pm_maintenance(book, market_by_name, ...)`` -- per-book aggregator
    (which itself *sums* per-position ``pm_stress_maintenance``).

:func:`combined_maintenance` is the ONLY place that computes "all subset legs as
ONE leg-set" -- that is the netting-benefit number (hedged longs/shorts net down
the worst-case stress decline). The comparison table's ``maintenance`` column
uses ``margin.pm_maintenance`` (per-position sum) so it stays consistent with the
book-level report; ``combined_maintenance`` exposes the netting delta separately.

Root-finding (decisive ruling 3)
--------------------------------
No scipy. The liquidation root is found by sweeping a spot grid for the first
sign change of Excess-Liquidity, then bisecting to ~1e-3 of spot. Single-name
books solve on that one spot; multi-name books perturb every underlying by the
same fractional move (v1 scope, documented at the call site).

Units (decisive rulings 4 & 5)
------------------------------
  * ``iv_shift`` is in vol POINTS (matches ``scenario._bump_vol``): ``+5 => +0.05``.
  * Net delta-equivalent shares is ``dollar_greeks['delta']`` directly -- it is
    already share-equivalent (per-share * mult * qty); never multiplied by 100.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from bursahack.options import margin, position, risk
from bursahack.options.types import Account, Book, Leg, MarketState, Position

__all__ = [
    "PositionRef",
    "position_index",
    "select_subset",
    "subset_to_legs",
    "subset_impact",
    "liquidation_point",
    "compare_subsets",
    "combined_maintenance",
]


# ---------------------------------------------------------------------------
# Position identity (shared by combine + CLI)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PositionRef:
    """Stable 1-based reference for a position in book order.

    ``id`` is the 1-based index (book order); ``name`` is the human label
    (``pos.name`` else ``pos.underlying`` else ``"Position <id>"``).
    """

    id: int
    name: str
    underlying: str
    n_legs: int
    net_qty: float


def _position_label(pos: Position, pid: int) -> str:
    """Label rule: ``name`` -> ``underlying`` -> ``"Position <id>"``."""
    return (getattr(pos, "name", "") or "") or (getattr(pos, "underlying", "") or "") or f"Position {pid}"


def position_index(book: Book) -> tuple[PositionRef, ...]:
    """1-based stable id + label per position, in book order (id == enumerate 1)."""
    refs: list[PositionRef] = []
    for pid, pos in enumerate(book.positions, start=1):
        refs.append(
            PositionRef(
                id=pid,
                name=_position_label(pos, pid),
                underlying=getattr(pos, "underlying", "") or "",
                n_legs=len(pos.legs),
                net_qty=float(pos.net_qty),
            )
        )
    return tuple(refs)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def select_subset(book: Book, ids: list[int]) -> Book:
    """Pick 1-based ``ids`` (book order) into a new frozen :class:`Book`.

    Validates: every id is an int in ``[1, N]`` with no duplicates. Positions are
    returned in the *id order given*, and ``book.asof`` is preserved.
    """
    n = len(book.positions)
    coerced: list[int] = []
    for raw in ids:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(
                f"subset ids must be 1-based ints within [1, {n}]; got {raw!r}"
            )
        if raw < 1 or raw > n:
            raise ValueError(
                f"subset ids must be 1-based within [1, {n}]; got {raw}"
            )
        coerced.append(raw)
    seen: set[int] = set()
    for i in coerced:
        if i in seen:
            raise ValueError(f"duplicate id {i} in subset {ids}")
        seen.add(i)
    chosen = tuple(book.positions[i - 1] for i in coerced)
    return Book(positions=chosen, asof=book.asof)


def subset_to_legs(book_or_subset) -> list[Leg]:
    """Flatten a Book / Position / iterable-of-Position / iterable-of-Leg to legs.

    Order is preserved. This is the leg-set passed to risk / payoff / prob calls.
    """
    # Book (has .positions)
    positions = getattr(book_or_subset, "positions", None)
    if positions is not None:
        out: list[Leg] = []
        for pos in positions:
            out.extend(pos.legs)
        return out
    # Single Position (has .legs)
    legs = getattr(book_or_subset, "legs", None)
    if legs is not None:
        return list(legs)
    # Iterable of Position or Leg
    if isinstance(book_or_subset, Iterable):
        out2: list[Leg] = []
        for item in book_or_subset:
            item_legs = getattr(item, "legs", None)
            if item_legs is not None:           # Position
                out2.extend(item_legs)
            else:                                # assume Leg
                out2.append(item)
        return out2
    raise TypeError(
        f"subset_to_legs: unsupported type {type(book_or_subset)!r}"
    )


# ---------------------------------------------------------------------------
# Market-map resolution (single MarketState alias => single-name subset)
# ---------------------------------------------------------------------------
def _underlyings(sub: Book) -> list[str]:
    """Distinct underlyings in a (sub)book, in first-seen order."""
    seen: list[str] = []
    for pos in sub.positions:
        sym = pos.underlying or ""
        if sym not in seen:
            seen.append(sym)
    return seen


def _resolve_market_map(
    sub: Book, market: MarketState | dict[str, MarketState]
) -> dict[str, MarketState]:
    """Resolve a per-underlying market map for ``sub``.

    A bare ``MarketState`` is only valid for a single-underlying subset (it is
    mapped onto that one name). A dict must cover every underlying in the subset.
    """
    syms = _underlyings(sub)
    if isinstance(market, dict):
        missing = [s for s in syms if s not in market]
        if missing:
            raise KeyError(
                f"market map missing MarketState for {missing}; have {sorted(market)}"
            )
        return {s: market[s] for s in syms}
    # single MarketState
    if len(syms) != 1:
        raise ValueError(
            "a single MarketState only resolves a single-underlying subset; "
            f"this subset spans {syms} -- pass a dict[str, MarketState]"
        )
    return {syms[0]: market}


def _perturb_markets(
    market_map: dict[str, MarketState], frac: float, iv_shift_pts: float = 0.0
) -> dict[str, MarketState]:
    """Scale every spot by ``(1 + frac)`` and bump sigma by ``iv_shift_pts/100``."""
    out: dict[str, MarketState] = {}
    for sym, mkt in market_map.items():
        new_spot = max(mkt.spot * (1.0 + frac), 1e-9)
        new_sigma = max(mkt.sigma + iv_shift_pts / 100.0, 0.0)
        out[sym] = replace(mkt, spot=new_spot, sigma=new_sigma)
    return out


def _combined_value(sub: Book, market_map: dict[str, MarketState], dt_days: float) -> float:
    """Sum of signed ``value_position`` across the subset at the given market map."""
    total = 0.0
    for pos in sub.positions:
        mkt = market_map[pos.underlying]
        total += position.value_position(
            list(pos.legs), mkt.spot, mkt, dt_days=dt_days
        )["position_value"]
    return total


def _combined_net_cost(sub: Book) -> float:
    """Sum of ``net_cost_entry`` over every position in the subset."""
    return float(sum(position.net_cost_entry(list(pos.legs)) for pos in sub.positions))


def _combined_greeks(sub: Book, market_map: dict[str, MarketState], dt_days: float) -> dict[str, float]:
    """Sum of signed dollar greeks across the subset at the given market map."""
    keys = ("delta", "gamma", "theta_day", "theta_yr", "vega", "rho")
    agg = {k: 0.0 for k in keys}
    for pos in sub.positions:
        mkt = market_map[pos.underlying]
        dg = position.dollar_greeks(list(pos.legs), mkt.spot, mkt, dt_days=dt_days)
        for k in keys:
            agg[k] += float(dg.get(k, 0.0))
    return agg


# ---------------------------------------------------------------------------
# Subset impact (price-move sweep)
# ---------------------------------------------------------------------------
def subset_impact(
    book: Book,
    ids: list[int],
    market_by_name: dict[str, MarketState] | MarketState,
    account: Account,
    *,
    price_moves: tuple[float, ...] = (-0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, 0.30),
    iv_shift: float = 0.0,
    dt_days: float = 0,
) -> dict:
    """Combined P&L / greeks / maintenance / Excess-Liquidity curve for a subset
    over a grid of fractional price moves.

    For each move ``m`` every underlying's spot is scaled by ``(1 + m)`` (via
    ``dataclasses.replace``) and sigma bumped by ``iv_shift`` vol POINTS. The
    combined P&L is ``Σ value_position(legs, S', mkt') − Σ net_cost_entry(legs)``;
    maintenance is ``margin.pm_maintenance`` on the subset-book at the perturbed
    market; Excess-Liquidity is ``netliq_at_move − maintenance`` where
    ``netliq_at_move = account.netliq + (combined_value(S') − combined_value(S0))``.
    """
    sub = select_subset(book, ids)
    refs = position_index(book)
    labels = [refs[i - 1].name for i in ids]
    base_map = _resolve_market_map(sub, market_by_name)
    netliq = float(getattr(account, "netliq", 0.0) or 0.0)
    cash = float(getattr(account, "cash", 0.0) or 0.0)

    net_cost = _combined_net_cost(sub)
    base_value = _combined_value(sub, base_map, dt_days)

    base_spot = {sym: mkt.spot for sym, mkt in base_map.items()}

    curve: list[dict] = []
    greeks_at_base: dict[str, float] = {}
    for m in price_moves:
        pmap = _perturb_markets(base_map, m, iv_shift)
        val = _combined_value(sub, pmap, dt_days)
        pnl = val - net_cost
        greeks = _combined_greeks(sub, pmap, dt_days)
        netliq_at_move = netliq + (val - base_value)
        maint = margin.pm_maintenance(sub, pmap, cash=cash, netliq=netliq_at_move)["maintenance"]
        excess = netliq_at_move - maint
        if m == 0.0:
            greeks_at_base = {
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "theta_day": greeks["theta_day"],
                "vega": greeks["vega"],
                "rho": greeks["rho"],
            }
        curve.append({
            "move": float(m),
            "spot": {sym: mkt.spot for sym, mkt in pmap.items()},
            "pnl": float(pnl),
            "delta": float(greeks["delta"]),
            "gamma": float(greeks["gamma"]),
            "theta_day": float(greeks["theta_day"]),
            "vega": float(greeks["vega"]),
            "rho": float(greeks["rho"]),
            "delta_equiv_shares": float(greeks["delta"]),
            "maintenance": float(maint),
            "excess_liquidity": float(excess),
        })

    if not greeks_at_base:
        # move 0.0 was not in the grid; compute it explicitly for the summary.
        g0 = _combined_greeks(sub, base_map, dt_days)
        greeks_at_base = {
            "delta": g0["delta"], "gamma": g0["gamma"],
            "theta_day": g0["theta_day"], "vega": g0["vega"], "rho": g0["rho"],
        }

    return {
        "ids": list(ids),
        "labels": labels,
        "base_spot": base_spot,
        "price_moves": [float(m) for m in price_moves],
        "curve": curve,
        "net_cost_entry": float(net_cost),
        "greeks_at_base": greeks_at_base,
        "iv_shift": float(iv_shift),
        "dt_days": float(dt_days),
    }


# ---------------------------------------------------------------------------
# Liquidation point (EL -> 0 in spot)
# ---------------------------------------------------------------------------
def _book_from(book_or_legs) -> Book:
    """Coerce a Book / Position / list-of-Position / list-of-Leg into a Book.

    A loose leg-set (or single Position) becomes a one-position Book grouped by
    the first leg's underlying so ``margin.pm_maintenance`` can stress it.
    """
    if getattr(book_or_legs, "positions", None) is not None:
        return book_or_legs  # already a Book
    legs = subset_to_legs(book_or_legs)
    if not legs:
        raise ValueError("liquidation_point: empty leg-set")
    under = getattr(legs[0], "underlying", "") or ""
    return Book(positions=(Position(underlying=under, legs=tuple(legs)),))


def liquidation_point(
    book_or_legs,
    market: MarketState | dict[str, MarketState],
    account: Account,
    *,
    direction: str = "both",
    span: float = 0.95,
    steps: int = 600,
) -> dict:
    """Solve the spot(s) at which Excess-Liquidity hits zero (a margin call).

    ``EL(S') = NetLiq(S') − maintenance(S')`` where
    ``NetLiq(S') = account.netliq + Σ_pos (value_position(legs, S', mkt')
    − value_position(legs, S0, mkt0))`` and ``maintenance(S')`` is the book PM
    maintenance recomputed at ``S'`` via ``margin.pm_maintenance`` on a
    ``replace``-perturbed market map.

    Single-underlying book -> sweep that one spot. Multi-underlying book ->
    perturb ALL underlyings by the same fractional move (v1 scope; per-name
    solving is out of scope). Each side is swept over a spot grid; the first
    sign change of ``EL`` is bisected to ~1e-3 of spot (no scipy).
    """
    sub = _book_from(book_or_legs)
    base_map = _resolve_market_map(sub, market)
    netliq = float(getattr(account, "netliq", 0.0) or 0.0)
    cash = float(getattr(account, "cash", 0.0) or 0.0)
    primary = _underlyings(sub)[0]
    base_spot = base_map[primary].spot

    base_value = _combined_value(sub, base_map, 0.0)

    def el_at_frac(frac: float) -> float:
        pmap = _perturb_markets(base_map, frac, 0.0)
        val = _combined_value(sub, pmap, 0.0)
        netliq_move = netliq + (val - base_value)
        maint = margin.pm_maintenance(sub, pmap, cash=cash, netliq=netliq_move)["maintenance"]
        return netliq_move - maint

    el_base = el_at_frac(0.0)

    def _solve_side(side: str) -> dict | None:
        # Build the fractional sweep going outward from 0 on the chosen side.
        if side == "down":
            fracs = [-span * (k / steps) for k in range(1, steps + 1)]  # 0- toward -span
        else:  # up
            # allow up to +span*2 headroom: short-call up-tails breach further out.
            fracs = [span * 2.0 * (k / steps) for k in range(1, steps + 1)]
        prev_f = 0.0
        prev_el = el_base
        for f in fracs:
            cur = el_at_frac(f)
            if prev_el > 0.0 >= cur or (prev_el > 0.0 and cur <= 0.0):
                # bisect between prev_f and f for EL == 0
                lo, hi = prev_f, f
                el_lo, el_hi = prev_el, cur
                for _ in range(80):
                    mid = 0.5 * (lo + hi)
                    el_mid = el_at_frac(mid)
                    if abs(hi - lo) * abs(base_spot) <= 1e-3:
                        break
                    if (el_lo > 0.0) == (el_mid > 0.0):
                        lo, el_lo = mid, el_mid
                    else:
                        hi, el_hi = mid, el_mid
                root_f = 0.5 * (lo + hi)
                spot = base_spot * (1.0 + root_f)
                return {"spot": float(spot), "pct": float(root_f), "el_at": float(el_at_frac(root_f))}
            prev_f, prev_el = f, cur
        return None

    down = up = None
    if direction in ("down", "both"):
        down = _solve_side("down")
    if direction in ("up", "both"):
        up = _solve_side("up")

    notes: list[str] = []
    if direction in ("down", "both") and down is None:
        notes.append("EL never reaches 0 within span on the down side")
    if direction in ("up", "both") and up is None:
        notes.append("EL never reaches 0 within span on the up side")

    return {
        "base_spot": float(base_spot),
        "el_base": float(el_base),
        "down": down,
        "up": up,
        "direction": direction,
        "note": "; ".join(notes),
    }


# ---------------------------------------------------------------------------
# Combined maintenance (netting benefit)
# ---------------------------------------------------------------------------
def combined_maintenance(book: Book, ids: list[int], market: MarketState | dict[str, MarketState]) -> dict:
    """Netting benefit of treating the subset's legs as ONE stress set.

    ``combined``        = ``risk.pm_stress_maintenance(all_subset_legs)`` at the
                          primary underlying spot (one leg-set -> longs net shorts).
    ``sum_per_position``= ``Σ risk.pm_stress_maintenance(pos.legs)`` (the per-
                          position view ``margin.pm_maintenance`` uses).
    ``netting_benefit`` = ``sum_per_position − combined`` (>= 0 for a hedged pair).

    This is the ONLY place that combines legs across positions into one stress
    set (decisive ruling 1); it is what the hedged-pair test asserts
    ``combined < sum_per_position`` on.
    """
    sub = select_subset(book, ids)
    market_map = _resolve_market_map(sub, market)
    # combined: all legs as one set, stressed at the primary underlying spot.
    primary = _underlyings(sub)[0]
    spot = market_map[primary].spot
    all_legs = subset_to_legs(sub)
    combined = float(risk.pm_stress_maintenance(all_legs, spot)["maintenance"])
    sum_pp = 0.0
    for pos in sub.positions:
        mkt = market_map[pos.underlying]
        sum_pp += float(risk.pm_stress_maintenance(list(pos.legs), mkt.spot)["maintenance"])
    return {
        "combined": combined,
        "sum_per_position": float(sum_pp),
        "netting_benefit": float(sum_pp - combined),
    }


# ---------------------------------------------------------------------------
# Compare subsets
# ---------------------------------------------------------------------------
_COMPARE_COLUMNS = (
    "label",
    "net_cost_entry",
    "pnl_base",
    "delta",
    "gamma",
    "theta_day",
    "vega",
    "rho",
    "delta_equiv_shares",
    "maintenance",
    "excess_liquidity",
    "max_loss",
    "max_profit",
    "defined_risk",
    "liquidation_down_pct",
    "liquidation_up_pct",
)


def compare_subsets(book: Book, market, account: Account, id_sets: list[list[int]]) -> dict:
    """Structured comparison table, one row per id-subset.

    The ``maintenance`` column uses ``margin.pm_maintenance`` (per-position sum)
    so it is consistent with the book-level report; the netting-benefit view is
    :func:`combined_maintenance`. ``max_loss`` / ``max_profit`` / ``defined_risk``
    come from ``risk.defined_risk`` on the flattened subset legs.
    """
    refs = position_index(book)
    netliq = float(getattr(account, "netliq", 0.0) or 0.0)
    cash = float(getattr(account, "cash", 0.0) or 0.0)

    rows: list[dict] = []
    for ids in id_sets:
        sub = select_subset(book, ids)
        market_map = _resolve_market_map(sub, market)
        labels = [refs[i - 1].name for i in ids]

        net_cost = _combined_net_cost(sub)
        base_value = _combined_value(sub, market_map, 0.0)
        pnl_base = base_value - net_cost
        greeks = _combined_greeks(sub, market_map, 0.0)
        maint = margin.pm_maintenance(sub, market_map, cash=cash, netliq=netliq)["maintenance"]
        excess = netliq - maint

        dr = risk.defined_risk(subset_to_legs(sub))
        liq = liquidation_point(sub, market, account, direction="both")
        down_pct = liq["down"]["pct"] if liq["down"] else None
        up_pct = liq["up"]["pct"] if liq["up"] else None

        rows.append({
            "ids": list(ids),
            "labels": labels,
            "label": ",".join(str(i) for i in ids),
            "net_cost_entry": float(net_cost),
            "pnl_base": float(pnl_base),
            "delta": float(greeks["delta"]),
            "gamma": float(greeks["gamma"]),
            "theta_day": float(greeks["theta_day"]),
            "vega": float(greeks["vega"]),
            "rho": float(greeks["rho"]),
            "delta_equiv_shares": float(greeks["delta"]),
            "maintenance": float(maint),
            "excess_liquidity": float(excess),
            "max_loss": dr.max_loss,
            "max_profit": dr.max_profit,
            "defined_risk": bool(dr.defined_risk),
            "liquidation_down_pct": down_pct,
            "liquidation_up_pct": up_pct,
        })

    return {"subsets": rows, "columns": list(_COMPARE_COLUMNS)}
