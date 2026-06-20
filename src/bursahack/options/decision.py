"""Decision layer over a Book subset: scorecard, liquidation-prob, ranking.

This is the *decision* half of the combination/decision layer on top of the
GREEN ``bursahack.options`` core. It composes the analytic-probability module
(``prob``), the Monte-Carlo engine (``mc``), the risk lens (``risk``), the margin
aggregator (``margin``) and the combination primitives (``combine``) into three
public surfaces:

  * :func:`scorecard`        -- a flat metrics dict for a subset over a horizon.
  * :func:`prob_liquidation` -- MC first-passage P(margin call) before a horizon.
  * :func:`rank_decisions`   -- normalise + weight scorecards into a ranking.

Measure boundary (L6)
---------------------
``prob.resolve_drift`` is called exactly once at the public boundary of each
function that needs a drift; the resolved drift is then handed to ``mc`` /
``prob`` kernels. ``measure`` is a :class:`Measure`; ``mu`` is the real-world
drift (required only for ``Measure.REAL_WORLD``).

No path engine in the core (decisive ruling 2)
----------------------------------------------
``montecarlo.simulate_terminal`` is terminal-only and stays that way (221 tests
depend on it). :func:`prob_liquidation` builds its OWN seeded, antithetic GBM
*stepper* locally for the first-passage walk -- it does not touch ``montecarlo``.
Maintenance is recomputed along the path via ``risk.pm_stress_maintenance``
(intrinsic, time-independent -- documented); reduced time enters only NetLiq via
``position.value_position(dt_days=elapsed)``.

Monotonicity contract
---------------------
:func:`prob_liquidation` is non-decreasing in ``sigma`` at a fixed seed: the GBM
diffusion term scales with ``sigma`` against a fixed barrier, so on identical
shocks more paths breach as vol rises.
"""
from __future__ import annotations

import math

import numpy as np

from bursahack.options import combine, margin, position, prob, risk
from bursahack.options import montecarlo as mc
from bursahack.options.types import Account, Book, MarketState, Measure

__all__ = [
    "scorecard",
    "prob_liquidation",
    "rank_decisions",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _measure(measure) -> Measure:
    """Coerce a str / Measure into a :class:`Measure`."""
    if isinstance(measure, Measure):
        return measure
    return Measure(str(measure))


def _min_leg_T(sub: Book, market_map: dict[str, MarketState], horizon_T: float) -> float:
    """The min of ``horizon_T`` and the shortest leg time-to-expiry in the subset.

    Probability / MC horizons cannot exceed the first expiry (legs vanish), so we
    clamp ``T`` to the nearest leg expiry when it is sooner than the horizon.
    """
    t_min = horizon_T
    for pos in sub.positions:
        mkt = market_map[pos.underlying]
        asof = getattr(mkt, "asof", None)
        if asof is None:
            continue
        asof_date = asof.date()
        for leg in pos.legs:
            if leg.expiry is None:
                continue
            days = (leg.expiry - asof_date).days
            if days > 0:
                t_min = min(t_min, days / 365.0)
    return max(t_min, 1e-6)


def _subset_market(sub: Book, market) -> tuple[MarketState, str, dict[str, MarketState]]:
    """Resolve (primary MarketState, primary symbol, full map) for a subset."""
    market_map = combine._resolve_market_map(sub, market)
    primary = combine._underlyings(sub)[0]
    return market_map[primary], primary, market_map


def _combined_net_cost(sub: Book) -> float:
    return float(sum(position.net_cost_entry(list(pos.legs)) for pos in sub.positions))


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
def scorecard(
    book: Book,
    ids: list[int],
    market: MarketState | dict[str, MarketState],
    account: Account,
    *,
    horizon_days: float,
    measure: Measure = Measure.RISK_NEUTRAL,
    mu: float | None = None,
    n_paths: int = 100_000,
    seed: int = 7,
) -> dict:
    """A flat metrics dict for a subset over ``horizon_days``.

    Routes every probability through ``prob`` (measure-aware, drift resolved once
    here), every VaR/CVaR through an MC P&L distribution, maintenance through
    ``margin.pm_maintenance``, and liquidation through ``combine.liquidation_point``
    + :func:`prob_liquidation`.
    """
    meas = _measure(measure)
    sub = combine.select_subset(book, ids)
    refs = combine.position_index(book)
    labels = [refs[i - 1].name for i in ids]

    mkt, primary, market_map = _subset_market(sub, market)
    S = mkt.spot
    mult = int(getattr(sub.positions[0].legs[0], "mult", 100) or 100)
    legs = combine.subset_to_legs(sub)
    net_cost = _combined_net_cost(sub)

    horizon_T = horizon_days / 365.0
    T = _min_leg_T(sub, market_map, horizon_T)

    drift = prob.resolve_drift(meas, mkt.r, mkt.q, mu)

    # --- EV (rn always; rw only when mu given) ---------------------------
    ev_rn = float(prob.expected_pnl(legs, S, T, mkt.r, mkt.sigma, mkt.q,
                                    Measure.RISK_NEUTRAL, None, mult)["e_pnl"])
    ev_rw = (
        float(prob.expected_pnl(legs, S, T, mkt.r, mkt.sigma, mkt.q,
                                Measure.REAL_WORLD, mu, mult)["e_pnl"])
        if mu is not None else None
    )

    # --- POP + payoff buckets -------------------------------------------
    pop = float(prob.pop_expiry(legs, S, T, mkt.sigma, mkt.r, mkt.q, mult, 0.0,
                                meas, mu)["pop"])
    buckets = prob.payoff_buckets(legs, S, T, mkt.sigma, drift, mult)
    p_max_profit = float(buckets["p_max_profit"])
    p_max_loss = float(buckets["p_max_loss"])

    # --- defined risk ----------------------------------------------------
    dr = risk.defined_risk(legs)
    max_loss = dr.max_loss
    max_profit = dr.max_profit
    defined = bool(dr.defined_risk)

    # --- VaR/CVaR via MC (floored to max_loss when finite) ---------------
    floor = float(max_loss) if isinstance(max_loss, (int, float)) and max_loss > 0 else None
    st = mc.simulate_terminal(S, T, mkt.sigma, drift, n=n_paths, seed=seed)
    dist = mc.pnl_distribution(legs, st, mult, net_cost, mkt.r, T,
                               alphas=(0.95, 0.99), floor=floor)
    var_95 = float(dist["var"]["0.95"])
    var_99 = float(dist["var"]["0.99"])
    cvar_95 = float(dist["cvar"]["0.95"])
    cvar_99 = float(dist["cvar"]["0.99"])

    # --- maintenance / buying power -------------------------------------
    netliq = float(getattr(account, "netliq", 0.0) or 0.0)
    cash = float(getattr(account, "cash", 0.0) or 0.0)
    bp_consumed = float(margin.pm_maintenance(sub, market_map, cash=cash, netliq=netliq)["maintenance"])
    excess_liq_after = netliq - bp_consumed

    # --- net greeks ------------------------------------------------------
    greeks = combine._combined_greeks(sub, market_map, 0.0)
    delta = float(greeks["delta"])
    vega = float(greeks["vega"])
    theta_day = float(greeks["theta_day"])

    # --- liquidation distance + probability -----------------------------
    liq = combine.liquidation_point(sub, market, account, direction="both")
    liquidation_down_pct = liq["down"]["pct"] if liq["down"] else None
    liquidation_up_pct = liq["up"]["pct"] if liq["up"] else None
    pliq = prob_liquidation(sub, market, account, horizon_days=horizon_days,
                            measure=meas, mu=mu, seed=seed)
    liquidation_prob = float(pliq["prob_liquidation"])

    # --- efficiency ratios ----------------------------------------------
    capital_efficiency = (ev_rn / bp_consumed) if bp_consumed not in (0, 0.0) else None
    if isinstance(max_loss, (int, float)) and max_loss > 0 and isinstance(max_profit, (int, float)):
        reward_risk = float(max_profit) / float(max_loss)
    elif isinstance(max_profit, (int, float)) and (not isinstance(max_loss, (int, float)) or max_loss == 0):
        reward_risk = math.inf
    else:
        reward_risk = None

    return {
        "ids": list(ids),
        "labels": labels,
        "horizon_days": float(horizon_days),
        "measure": meas.value,
        "T": float(T),
        "ev_rn": ev_rn,
        "ev_rw": ev_rw,
        "pop": pop,
        "p_max_profit": p_max_profit,
        "p_max_loss": p_max_loss,
        "var_95": var_95,
        "var_99": var_99,
        "cvar_95": cvar_95,
        "cvar_99": cvar_99,
        "max_loss": max_loss,
        "max_profit": max_profit,
        "defined_risk": defined,
        "bp_consumed": bp_consumed,
        "excess_liq_after": float(excess_liq_after),
        "liquidation_down_pct": liquidation_down_pct,
        "liquidation_up_pct": liquidation_up_pct,
        "liquidation_prob": liquidation_prob,
        "delta": delta,
        "vega": vega,
        "theta_day": theta_day,
        "capital_efficiency": capital_efficiency,
        "reward_risk": reward_risk,
    }


# ---------------------------------------------------------------------------
# Probability of liquidation (MC first-passage)
# ---------------------------------------------------------------------------
def prob_liquidation(
    book_or_legs,
    market: MarketState | dict,
    account: Account,
    *,
    horizon_days: float,
    n_paths: int = 20_000,
    n_steps: int = 60,
    measure: Measure = Measure.RISK_NEUTRAL,
    mu: float | None = None,
    seed: int = 7,
) -> dict:
    """Monte-Carlo first-passage probability of a margin call before the horizon.

    Builds a local seeded antithetic GBM stepper (no core path engine):
    ``S_{t+1} = S_t * exp((drift - 0.5 sigma^2) dt + sigma sqrt(dt) Z)``. At each
    step ``EL = NetLiq(S_t) − maintenance(S_t)`` where NetLiq uses
    ``value_position(dt_days=elapsed)`` (time decays) and maintenance is the
    intrinsic ``pm_stress_maintenance`` (time-independent -- documented). First
    passage is the first step with ``EL <= 0``; vectorised across paths.

    Non-decreasing in ``sigma`` at a fixed seed (the diffusion term scales with
    ``sigma`` against a fixed barrier on identical shocks).
    """
    meas = _measure(measure)
    sub = combine._book_from(book_or_legs)
    mkt, primary, market_map = _subset_market(sub, market)
    S0 = mkt.spot
    sigma = mkt.sigma
    netliq = float(getattr(account, "netliq", 0.0) or 0.0)
    cash = float(getattr(account, "cash", 0.0) or 0.0)

    horizon_T = horizon_days / 365.0
    T = _min_leg_T(sub, market_map, horizon_T)
    drift = prob.resolve_drift(meas, mkt.r, mkt.q, mu)

    dt = T / n_steps
    sqrt_dt = math.sqrt(dt)
    rng = np.random.default_rng(seed)

    # base NetLiq anchor: combined value at S0, no decay. Per-path repricing along
    # the walk uses a coarse spot grid + linear interpolation (see step loop).
    base_value = sum(
        position.value_position(list(pos.legs), market_map[pos.underlying].spot,
                                market_map[pos.underlying], dt_days=0.0)["position_value"]
        for pos in sub.positions
    )

    # antithetic shocks: (n_paths x n_steps); mirror the first half.
    half = (n_paths + 1) // 2
    z_half = rng.standard_normal((half, n_steps))
    z = np.concatenate([z_half, -z_half], axis=0)[:n_paths]

    log_incr = (drift - 0.5 * sigma * sigma) * dt + sigma * sqrt_dt * z
    log_paths = np.cumsum(log_incr, axis=1)
    paths = S0 * np.exp(log_paths)            # (n_paths x n_steps), step 1..n_steps

    touched = np.zeros(n_paths, dtype=bool)
    touch_step = np.full(n_paths, -1, dtype=int)

    for step in range(n_steps):
        elapsed_days = (step + 1) * dt * 365.0
        spots = paths[:, step]
        # combined value across paths at this step (per-position, vectorised)
        val = np.zeros(n_paths, dtype=float)
        maint = np.zeros(n_paths, dtype=float)
        # group identical-spot evaluation by sampling on a coarse spot grid then
        # interpolate would lose accuracy; evaluate per-position over the path
        # using value_position which is scalar -> use a spot-grid cache.
        uniq_idx = _grid_cache_indices(spots)
        grid_spots = spots[uniq_idx]
        val_grid = np.zeros(grid_spots.shape[0])
        maint_grid = np.zeros(grid_spots.shape[0])
        for gi, s in enumerate(grid_spots):
            v = 0.0
            for pos in sub.positions:
                pm = _scaled_market(market_map[pos.underlying], s / S0)
                v += position.value_position(list(pos.legs), pm.spot, pm, dt_days=elapsed_days)["position_value"]
            val_grid[gi] = v
            # maintenance: intrinsic stress at the stepped spot (time-independent)
            mm = 0.0
            for pos in sub.positions:
                pm = _scaled_market(market_map[pos.underlying], s / S0)
                mm += float(risk.pm_stress_maintenance(list(pos.legs), pm.spot)["maintenance"])
            maint_grid[gi] = mm
        val = _grid_lookup(spots, grid_spots, val_grid)
        maint = _grid_lookup(spots, grid_spots, maint_grid)

        netliq_step = netliq + (val - base_value)
        el = netliq_step - maint
        newly = (~touched) & (el <= 0.0)
        touch_step[newly] = step + 1
        touched |= newly

    prob_liq = float(touched.mean())
    if touched.any():
        expected_ttt = float((touch_step[touched].astype(float) * dt * 365.0).mean())
    else:
        expected_ttt = None

    terminal_spots = paths[:, -1]
    el_terminal = _terminal_el(sub, market_map, S0, base_value, netliq, terminal_spots, T)

    return {
        "prob_liquidation": prob_liq,
        "expected_time_to_touch_days": expected_ttt,
        "terminal": {
            "mean": float(terminal_spots.mean()),
            "p5": float(np.percentile(terminal_spots, 5)),
            "p50": float(np.percentile(terminal_spots, 50)),
            "p95": float(np.percentile(terminal_spots, 95)),
        },
        "el_terminal": {
            "mean": float(el_terminal.mean()),
            "p5": float(np.percentile(el_terminal, 5)),
        },
        "n_paths": int(n_paths),
        "n_steps": int(n_steps),
        "horizon_days": float(horizon_days),
        "measure": meas.value,
    }


def _scaled_market(mkt: MarketState, frac_of_base: float) -> MarketState:
    """A market with spot = base_spot * frac_of_base (sigma/r/q unchanged)."""
    from dataclasses import replace
    return replace(mkt, spot=max(mkt.spot * frac_of_base, 1e-9))


def _grid_cache_indices(spots: np.ndarray, n_grid: int = 80) -> np.ndarray:
    """Indices into ``spots`` of a coarse representative spot grid.

    Repricing the position at every path-spot is O(n_paths * n_legs); instead we
    reprice on a coarse monotone grid of ``n_grid`` quantile spots and linearly
    interpolate (the position value is piecewise-smooth in spot). Returns the
    indices (sorted by spot) of the chosen grid nodes.
    """
    n = spots.shape[0]
    if n <= n_grid:
        return np.argsort(spots)
    order = np.argsort(spots)
    pick = np.linspace(0, n - 1, n_grid).round().astype(int)
    return order[pick]


def _grid_lookup(spots: np.ndarray, grid_spots: np.ndarray, grid_vals: np.ndarray) -> np.ndarray:
    """Linear-interpolate ``grid_vals`` (defined on sorted ``grid_spots``) onto ``spots``."""
    return np.interp(spots, grid_spots, grid_vals)


def _terminal_el(sub: Book, market_map, S0: float, base_value: float,
                 netliq: float, terminal_spots: np.ndarray, T: float) -> np.ndarray:
    """Excess-Liquidity at the horizon across paths (for el_terminal stats)."""
    uniq_idx = _grid_cache_indices(terminal_spots)
    grid_spots = terminal_spots[uniq_idx]
    elapsed_days = T * 365.0
    val_grid = np.zeros(grid_spots.shape[0])
    maint_grid = np.zeros(grid_spots.shape[0])
    for gi, s in enumerate(grid_spots):
        v = mm = 0.0
        for pos in sub.positions:
            pm = _scaled_market(market_map[pos.underlying], s / S0)
            v += position.value_position(list(pos.legs), pm.spot, pm, dt_days=elapsed_days)["position_value"]
            mm += float(risk.pm_stress_maintenance(list(pos.legs), pm.spot)["maintenance"])
        val_grid[gi] = v
        maint_grid[gi] = mm
    val = _grid_lookup(terminal_spots, grid_spots, val_grid)
    maint = _grid_lookup(terminal_spots, grid_spots, maint_grid)
    return (netliq + (val - base_value)) - maint


# ---------------------------------------------------------------------------
# Rank
# ---------------------------------------------------------------------------
_DEFAULT_WEIGHTS = {
    "ev_rn": 0.30,
    "pop": 0.20,
    "capital_efficiency": 0.20,
    "reward_risk": 0.15,
    "liquidation_prob": -0.15,
}


def _norm(values: list[float]) -> list[float]:
    """Min-max normalise to [0, 1]; a flat column maps to all 0.5 (neutral)."""
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return [0.5 for _ in values]
    lo, hi = min(finite), max(finite)
    span = hi - lo
    out: list[float] = []
    for v in values:
        if v is None or not math.isfinite(v):
            # non-finite reward_risk (defined-risk "free" structure) -> best (1.0)
            out.append(1.0 if (v is not None and v == math.inf) else 0.0)
        elif span <= 0:
            out.append(0.5)
        else:
            out.append((v - lo) / span)
    return out


def rank_decisions(
    book: Book,
    market,
    account: Account,
    candidates: list[list[int]],
    *,
    weights: dict[str, float] | None = None,
    horizon_days: float = 30,
    measure: Measure = Measure.RISK_NEUTRAL,
    mu: float | None = None,
    seed: int = 7,
) -> dict:
    """Score, normalise and weight candidate subsets into a deterministic ranking.

    Each candidate is scored via :func:`scorecard`; each weighted metric is
    min-max normalised across candidates; the weighted sum is the score. Sort is
    stable (score desc, tie-break by candidate index) -- determinism is a test.
    """
    meas = _measure(measure)
    w = dict(_DEFAULT_WEIGHTS if weights is None else weights)

    cards = [
        scorecard(book, ids, market, account, horizon_days=horizon_days,
                  measure=meas, mu=mu, seed=seed)
        for ids in candidates
    ]

    # Normalise each weighted metric column across candidates.
    norm_cols: dict[str, list[float]] = {}
    for metric in w:
        col = [c.get(metric) for c in cards]
        norm_cols[metric] = _norm(col)

    scored: list[tuple[float, int, dict, dict]] = []
    for idx, (ids, card) in enumerate(zip(candidates, cards)):
        score = 0.0
        for metric, weight in w.items():
            score += weight * norm_cols[metric][idx]
        scored.append((score, idx, ids, card))

    # Stable sort: score desc, tie-break by original candidate index asc.
    scored.sort(key=lambda t: (-t[0], t[1]))

    ranked: list[dict] = []
    for rank, (score, idx, ids, card) in enumerate(scored, start=1):
        label = ",".join(str(i) for i in ids)
        ranked.append({
            "rank": rank,
            "ids": list(ids),
            "label": label,
            "score": float(score),
            "scorecard": card,
            "recommendation": _recommendation(label, card),
            "dominating_risk": _dominating_risk(card),
            "tradeoff": _tradeoff(card),
        })

    return {
        "ranked": ranked,
        "weights": w,
        "measure": meas.value,
        "horizon_days": float(horizon_days),
    }


# ---------------------------------------------------------------------------
# Deterministic verdict strings (derived from the numbers, no randomness)
# ---------------------------------------------------------------------------
def _recommendation(label: str, card: dict) -> str:
    pop = card.get("pop", 0.0)
    ev = card.get("ev_rn", 0.0)
    defined = card.get("defined_risk", False)
    risk_word = "defined-risk" if defined else "undefined-risk"
    return (
        f"Subset {label}: {risk_word}, POP {pop:.0%}, "
        f"E[P&L]_rn {ev:,.0f} over {card.get('horizon_days', 0):.0f}d."
    )


def _dominating_risk(card: dict) -> str:
    down = card.get("liquidation_down_pct")
    up = card.get("liquidation_up_pct")
    pliq = card.get("liquidation_prob", 0.0)
    if not card.get("defined_risk", False):
        side = "up-side" if up is not None or down is None else "down-side"
        return f"undefined {side} tail (liquidation prob {pliq:.1%})"
    if down is not None:
        return f"down-side liquidation at {down:.0%} spot (prob {pliq:.1%})"
    if up is not None:
        return f"up-side liquidation at {up:.0%} spot (prob {pliq:.1%})"
    return f"no liquidation breach within span (prob {pliq:.1%})"


def _tradeoff(card: dict) -> str:
    eff = card.get("capital_efficiency")
    el = card.get("excess_liq_after", 0.0)
    eff_str = f"{eff:.2f}" if isinstance(eff, (int, float)) and math.isfinite(eff) else "n/a"
    return f"capital efficiency {eff_str}, EL cushion after {el:,.0f}."
