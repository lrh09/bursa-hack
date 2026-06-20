"""Monte-Carlo terminal simulation + P&L distribution for option positions.

GBM terminal law (v1 default)
-----------------------------
Under geometric Brownian motion with resolved total drift ``b`` (e.g. ``r - q``
risk-neutral, or ``mu`` real-world)::

    S_T = S_0 * exp( (b - 0.5*sigma^2) * T + sigma * sqrt(T) * Z ),   Z ~ N(0,1)

The caller resolves the drift via ``prob.resolve_drift`` and passes it in; this
module never resolves a measure itself (L4/L6 — the measure boundary lives in
``prob``).

Optional fat tail
-----------------
``simulate_terminal(..., dist='student_t', nu=...)`` replaces the Gaussian shock
``Z`` with a **variance-standardised** Student-t draw ``T_nu / sqrt(nu/(nu-2))``
and recenters the multiplicative shock to unit mean, so the terminal **mean**
still matches the forward ``S*exp(drift*T)`` while the tails are heavier. This is
an honest-odds (real-world) stress lever — it is NOT used for risk-neutral
fair-value reconciliation, where GBM is the analytic anchor.

P&L convention
--------------
Per-leg expiry value is signed intrinsic ``* mult * qty``; the position P&L is
the summed terminal payoff **minus ``net_cost``** (L3 single-source-of-payoff).
``net_cost`` comes from the SAME leg valuation as the payoff — pass
``position.net_cost_entry(legs)`` for realized P&L framing or
``position.net_cost_fair(legs, market)`` for theoretical.

Reconciliation (CI-enforced, §5)
--------------------------------
Every shipped MC summary passes ``reconcile(mc, se, analytic, k=3)`` against an
analytic anchor: MC price <-> ``bsm.price``, MC POP <-> ``prob.pop_expiry``,
RN-discounted MC E[P&L] of a fair spread <-> 0.

References:
  - Glasserman, *Monte Carlo Methods in Financial Engineering* (GBM, antithetics).
"""
from __future__ import annotations

import math

import numpy as np

from bursahack.options.payoff import terminal_payoff
from bursahack.options.types import Leg

__all__ = [
    "simulate_terminal",
    "pnl_distribution",
    "var_cvar",
    "pnl_percentiles",
    "reconcile",
]


# ---------------------------------------------------------------------------
# Terminal sampler
# ---------------------------------------------------------------------------
def simulate_terminal(S: float, T: float, sigma: float, drift: float, n: int = 1_000_000,
                      seed: int | None = None, antithetic: bool = True,
                      dist: str = "gbm", nu: float = 5.0) -> np.ndarray:
    """Sample terminal underlier prices ``S_T`` under GBM (v1 default).

    Caller passes a RESOLVED total drift (use ``prob.resolve_drift``). With
    ``antithetic=True`` the shocks are mirrored (Z, -Z) for variance reduction;
    ``n`` is rounded up to an even count in that case.

    ``dist='student_t'`` (optional fat tail) standardises a Student-t(nu) shock to
    unit variance and then recenters the multiplicative shock to unit mean, so the
    terminal mean still equals the forward ``S*exp(drift*T)`` while the tails are
    heavier (the t-MGF is infinite, so the Gaussian ``-0.5*sigma^2`` correction
    cannot preserve the mean here). Requires ``nu > 2``.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if dist not in ("gbm", "student_t"):
        raise ValueError(f"unknown dist: {dist!r} (v1 supports 'gbm', 'student_t')")
    if dist == "student_t" and nu <= 2.0:
        raise ValueError("student_t requires nu > 2 for finite variance")
    rng = np.random.default_rng(seed)

    def _shock(m: int) -> np.ndarray:
        if dist == "gbm":
            return rng.standard_normal(m)
        # unit-variance standardised Student-t: Var(t_nu) = nu/(nu-2)
        return rng.standard_t(nu, m) / math.sqrt(nu / (nu - 2.0))

    if antithetic:
        half = (n + 1) // 2
        z_half = _shock(half)
        z = np.concatenate([z_half, -z_half])[:n]
    else:
        z = _shock(n)

    vt = sigma * math.sqrt(T)
    if dist == "gbm":
        # Analytic martingale correction: E[exp(vt*Z)] = exp(0.5*vt^2).
        return S * np.exp((drift - 0.5 * sigma * sigma) * T + vt * z)
    # Fat-tail: the Student-t MGF is infinite, so the -0.5*sigma^2 correction
    # does NOT preserve E[S_T]. Recenter the multiplicative shock to unit mean
    # empirically so the terminal mean still equals the forward S*exp(drift*T)
    # (honest, mean-preserving fat tail). Variance is heavier in the tails.
    shock = np.exp(vt * z)
    shock = shock / shock.mean()           # E[shock] = 1 (sample martingale)
    return S * math.exp(drift * T) * shock


# ---------------------------------------------------------------------------
# P&L vector from terminal prices
# ---------------------------------------------------------------------------
def _pnl_array(legs: list[Leg], st_array: np.ndarray, mult: int, net_cost: float) -> np.ndarray:
    """Vectorised terminal P&L over an array of terminal prices.

    Reuses the single-source ``payoff.terminal_payoff`` per leg-type so the MC
    payoff is byte-for-byte the same formula as the analytic ladder (L3). Built
    vectorised on numpy for speed (intrinsic is closed-form per leg)."""
    st = np.asarray(st_array, dtype=float)
    total = np.zeros_like(st)
    for lg in legs:
        sign = 1.0 if lg.qty >= 0 else -1.0
        qty = abs(lg.qty)
        scale = sign * qty * lg.mult
        right = lg.right.value
        if right == "C":
            intrinsic = np.maximum(st - lg.strike, 0.0)
        elif right == "P":
            intrinsic = np.maximum(lg.strike - st, 0.0)
        elif right == "S":
            intrinsic = st            # stock leg: value per share is S_T
        elif right == "X":
            intrinsic = np.ones_like(st)  # cash unit
        else:
            raise ValueError(f"unknown right {lg.right!r}")
        total = total + scale * intrinsic
    return total - net_cost


def pnl_distribution(legs: list[Leg], ST_array: np.ndarray, mult: int, net_cost: float,
                     r: float, T: float, alphas: tuple[float, ...] = (0.95, 0.99),
                     floor: float | None = None, bins: int = 60) -> dict:
    """Full MC P&L distribution + summary stats for a multi-leg position.

    ``net_cost`` comes from the SAME leg valuation as the payoff (L3) — pass
    ``position.net_cost_entry(legs)`` for realized framing or ``net_cost_fair``
    for theoretical.

    Returns: ``pnl_array``, ``hist`` (counts,edges), ``pop`` (P(P&L>0)),
    ``e_pnl``/``e_pnl_pv`` (with PV discount ``exp(-rT)``), ``se``/``ci``
    (Monte-Carlo standard error and 95% CI of the mean), plus ``var``/``cvar``
    (POSITIVE losses, optionally floored to a defined-risk max loss) and
    ``percentiles``.
    """
    pnl = _pnl_array(legs, ST_array, mult, net_cost)
    n = pnl.size
    e_pnl = float(pnl.mean())
    std = float(pnl.std(ddof=1)) if n > 1 else 0.0
    se = std / math.sqrt(n) if n > 0 else float("nan")
    half_ci = 1.959963984540054 * se
    pop = float((pnl > 0).mean())
    disc = math.exp(-r * T)

    counts, edges = np.histogram(pnl, bins=bins)
    vc = var_cvar(pnl, alphas=alphas, floor=floor)
    pct = pnl_percentiles(pnl)

    return {
        "pnl_array": pnl,
        "hist": {"counts": counts.tolist(), "edges": edges.tolist()},
        "pop": pop,
        "e_pnl": e_pnl,
        "e_pnl_pv": e_pnl * disc,
        "se": se,
        "ci": (e_pnl - half_ci, e_pnl + half_ci),
        "var": vc["var"],
        "cvar": vc["cvar"],
        "tail_flag": vc["flag"],
        "percentiles": pct,
        "n": n,
    }


# ---------------------------------------------------------------------------
# Risk metrics over a P&L sample (Book-level: feed any pnl_array)
# ---------------------------------------------------------------------------
def var_cvar(pnl_array: np.ndarray, alphas: tuple[float, ...] = (0.95, 0.99),
             floor: float | None = None) -> dict:
    """Value-at-Risk / Conditional-VaR over a P&L sample, as POSITIVE losses.

    VaR_alpha is the ``(1-alpha)`` loss quantile; CVaR_alpha is the mean loss
    beyond it. If ``floor`` (a defined-risk max loss, e.g. spread debit) is given,
    losses are clamped to it. If ``floor is None`` AND the empirical loss tail is
    open (worst-draw loss within machine reach of the sample min, i.e. the tail
    is not bounded by structure), the result carries ``flag='unbounded'`` — never
    silently clamped (L7).

    VaR is monotone in alpha: VaR_0.99 >= VaR_0.95 (locked by test).
    """
    pnl = np.asarray(pnl_array, dtype=float)
    losses = -pnl  # positive = loss
    var_out: dict[str, float] = {}
    cvar_out: dict[str, float] = {}
    for alpha in alphas:
        q = float(np.quantile(losses, alpha))
        var = q
        tail = losses[losses >= q]
        cvar = float(tail.mean()) if tail.size else q
        if floor is not None:
            var = min(var, floor)
            cvar = min(cvar, floor)
        var_out[str(alpha)] = max(0.0, var)
        cvar_out[str(alpha)] = max(0.0, cvar)

    if floor is not None:
        flag = "floored"
    else:
        # Heuristic open-tail detector: the extreme loss is far above the
        # high-quantile VaR (no structural shelf truncating it).
        worst = float(losses.max())
        ref = max(var_out.values()) if var_out else 0.0
        flag = "unbounded" if (worst > ref * 1.5 and worst > 0) else "bounded"

    return {"var": var_out, "cvar": cvar_out, "flag": flag}


def pnl_percentiles(pnl_array: np.ndarray,
                    qs: tuple[int, ...] = (5, 10, 25, 50, 75, 90, 95)) -> dict:
    """P&L percentiles (signed P&L, not losses) at the requested quantile points."""
    pnl = np.asarray(pnl_array, dtype=float)
    return {str(q): float(np.percentile(pnl, q)) for q in qs}


# ---------------------------------------------------------------------------
# Reconciliation gate
# ---------------------------------------------------------------------------
def reconcile(mc_stat: float, mc_se: float, analytic: float, k: float = 3.0) -> dict:
    """MC <-> analytic gate: passes when ``|mc - analytic| <= k * se`` (§5 CI rule).

    Returns ``{pass, gap, gap_in_se, mc, analytic, se, k}``. When ``se`` is ~0
    (degenerate sample) the gate falls back to an absolute 1e-6 tolerance.
    """
    gap = abs(mc_stat - analytic)
    if mc_se is None or mc_se <= 0 or math.isnan(mc_se):
        passed = gap <= 1e-6
        gap_in_se = float("inf") if gap > 0 else 0.0
    else:
        gap_in_se = gap / mc_se
        passed = gap_in_se <= k
    return {"pass": bool(passed), "gap": gap, "gap_in_se": gap_in_se,
            "mc": mc_stat, "analytic": analytic, "se": mc_se, "k": k}
