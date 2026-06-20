"""Analytic probabilities for option positions under a chosen measure.

All probabilities here are *analytic* (closed-form under GBM / lognormal terminal
law). Monte-Carlo lives in ``montecarlo.py``; this module is the analytic anchor
the MC reconciles against.

Lognormal terminal law
-----------------------
Under GBM with total drift ``b`` (the drift applied to the spot, e.g. ``r - q``
risk-neutral, or ``mu`` real-world) the log-return is normal::

    ln(S_T / S_0) ~ Normal( (b - 0.5*sigma^2) * T ,  sigma^2 * T )

so, writing  m(L) = ( ln(L/S) - (b - 0.5*sigma^2) * T ) / (sigma * sqrt(T)) :

    P(S_T <= L) = N( m(L) )
    P(S_T  > L) = N(-m(L))
    P(a < S_T <= b) = N( m(b) ) - N( m(a) )

Risk-neutral prob-ITM of a call is the familiar ``N(d2)`` (which is exactly
``P(S_T > K)`` with ``b = r - q``). It is NOT the delta ``N(d1)`` (L6).

Probability of touch (one-touch, Reiner-Rubinstein reflection principle)
------------------------------------------------------------------------
With log-drift ``nu = b - 0.5*sigma^2``, ``vt = sigma*sqrt(T)`` and barrier
``a = ln(H/S)``, the probability the running max (up) / min (down) reaches the
barrier *at any time* before ``T`` is::

    up   (H > S): N( (-a + nu*T)/vt ) + exp(2*nu*a/sigma^2) * N( (-a - nu*T)/vt )
    down (H < S): N( ( a - nu*T)/vt ) + exp(2*nu*a/sigma^2) * N( ( a + nu*T)/vt )

Touch probability is always >= the finish-on-the-far-side probability.

Measure boundary (FROZEN, L6 / P1-2)
------------------------------------
``resolve_drift`` is called **exactly once**, inside the public measure-aware
entry points (``prob_itm``, ``pop_expiry``, ``prob_touch``, ``expected_pnl``,
``terminal_risk_summary``). The low-level kernels (``prob_price_below``,
``prob_in_range``, ``payoff_buckets``) take an already-resolved ``drift`` and are
measure-agnostic.

References:
  - Hull, *Options, Futures, and Other Derivatives* (lognormal property; N(d2)).
  - Reiner & Rubinstein, "Breaking Down the Barriers", Risk (1991) — one-touch.
"""
from __future__ import annotations

import math

from bursahack.options.core import d1d2, norm_cdf
from bursahack.options.payoff import terminal_payoff
from bursahack.options.position import net_cost_entry
from bursahack.options.types import Leg, Measure, Right

_SQRT_EPS = 1e-12


# ---------------------------------------------------------------------------
# Measure boundary
# ---------------------------------------------------------------------------
def resolve_drift(measure: Measure, r: float, q: float, mu: float | None) -> float:
    """Resolve the total spot drift for the chosen measure (called once, L6).

    Risk-neutral -> ``r - q``. Real-world -> ``mu`` (raise if ``mu`` is None).
    """
    if measure == Measure.RISK_NEUTRAL:
        return r - q
    if measure == Measure.REAL_WORLD:
        if mu is None:
            raise ValueError("REAL_WORLD measure requires an explicit mu (real-world drift)")
        return mu
    raise ValueError(f"unknown measure: {measure!r}")


def _m(S: float, L: float, T: float, sigma: float, drift: float) -> float:
    """Standardised log-distance to level ``L`` (see module docstring)."""
    vt = sigma * math.sqrt(T)
    return (math.log(L / S) - (drift - 0.5 * sigma * sigma) * T) / vt


# ---------------------------------------------------------------------------
# Low-level kernels (take an already-resolved drift; measure-agnostic)
# ---------------------------------------------------------------------------
def prob_price_below(S: float, L: float, T: float, sigma: float, drift: float) -> float:
    """``P(S_T <= L)`` under GBM with resolved total drift ``drift``.

    Degenerate guards: ``L <= 0`` -> 0.0; zero total vol -> deterministic step
    around the forward ``S*exp(drift*T)``.
    """
    if L <= 0.0:
        return 0.0
    vt = sigma * math.sqrt(T)
    if vt < _SQRT_EPS:
        forward = S * math.exp(drift * T)
        return 1.0 if forward <= L else 0.0
    return norm_cdf(_m(S, L, T, sigma, drift))


def prob_in_range(S: float, a: float, b: float, T: float, sigma: float, drift: float) -> float:
    """``P(a < S_T <= b)`` under GBM with resolved total drift ``drift``."""
    lo, hi = (a, b) if a <= b else (b, a)
    return prob_price_below(S, hi, T, sigma, drift) - prob_price_below(S, lo, T, sigma, drift)


def payoff_buckets(legs: list[Leg], S: float, T: float, sigma: float, drift: float,
                   mult: int = 100) -> dict:
    """Probability mass over expiry P&L regions (max-profit / max-loss / interior).

    The terminal payoff (signed, net of entry debit/credit) is piecewise-linear
    with kinks at the leg strikes. We split the spot axis at those kinks, assign
    each interval the sign of the payoff at its midpoint, and accumulate
    probability mass. ``p_max_profit`` / ``p_max_loss`` are the masses on the two
    *flat asymptotic shelves* (the unbounded tails) when those shelves are the
    payoff extrema; otherwise they are reported as the masses on the
    profit-extreme / loss-extreme intervals. The buckets + the two tails sum to
    1.0 (locked by the iron-condor golden).

    Caller resolves the drift first (measure-agnostic kernel).
    """
    net_cost = net_cost_entry(legs)
    strikes = sorted({lg.strike for lg in legs if lg.strike > 0.0})

    # Edges: 0, every strike, +inf (represented by a far node for midpoint signs).
    edges = [0.0, *strikes]
    far = (strikes[-1] if strikes else S) * 4.0 + S
    edges.append(far)

    buckets: list[dict] = []
    p_profit_intervals = 0.0
    p_loss_intervals = 0.0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mid = 0.5 * (lo + hi)
        pnl_mid = terminal_payoff(legs, mid, net_cost)
        # mass of (lo, hi]; the last interval's mass is the right tail to +inf.
        if i == len(edges) - 2:
            mass = 1.0 - prob_price_below(S, lo, T, sigma, drift)
        elif i == 0:
            mass = prob_price_below(S, hi, T, sigma, drift)
        else:
            mass = prob_in_range(S, lo, hi, T, sigma, drift)
        sign = "profit" if pnl_mid > 0 else ("loss" if pnl_mid < 0 else "flat")
        buckets.append({"lo": lo, "hi": hi, "mid": mid, "pnl": pnl_mid,
                        "prob": mass, "sign": sign})
        if pnl_mid > 0:
            p_profit_intervals += mass
        elif pnl_mid < 0:
            p_loss_intervals += mass

    # Max-profit / max-loss SHELF masses: the flat outer tails (left=0..K1,
    # right=Kn..inf) carry the asymptotic payoff. Report the mass on the tail
    # whose payoff equals the global max (resp. min).
    pnls = [b["pnl"] for b in buckets]
    gmax, gmin = max(pnls), min(pnls)
    p_max_profit = sum(b["prob"] for b in buckets if math.isclose(b["pnl"], gmax, rel_tol=1e-9, abs_tol=1e-9))
    p_max_loss = sum(b["prob"] for b in buckets if math.isclose(b["pnl"], gmin, rel_tol=1e-9, abs_tol=1e-9))

    return {
        "p_max_profit": p_max_profit,
        "p_max_loss": p_max_loss,
        "p_profit": p_profit_intervals,
        "p_loss": p_loss_intervals,
        "buckets": buckets,
        "net_cost": net_cost,
    }


# ---------------------------------------------------------------------------
# Public, measure-aware (resolve_drift called here, exactly once)
# ---------------------------------------------------------------------------
def prob_itm(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
             right: Right = Right.CALL,
             measure: Measure = Measure.RISK_NEUTRAL, mu: float | None = None) -> dict:
    """Probability the option finishes in-the-money.

    Call ITM = ``P(S_T > K)``; put ITM = ``P(S_T < K)``. Under the risk-neutral
    measure the call value is exactly ``N(d2)`` (NOT delta ``N(d1)``, L6).

    GOLDEN: P(S_T>460) RN = N(d2) = 0.3113 at S=389.80,T=1,sigma=0.46,r=0.045,q=0.
    """
    drift = resolve_drift(measure, r, q, mu)
    dd = d1d2(S, K, T, drift, 0.0, sigma)  # pass drift as (r-q) so d2 = N(d2) for this measure
    p_above = norm_cdf(dd.d2)              # = P(S_T > K)
    if right == Right.CALL:
        itm, otm = p_above, 1.0 - p_above
    else:
        itm, otm = 1.0 - p_above, p_above
    return {"prob_itm": itm, "prob_otm": otm, "d1": dd.d1, "d2": dd.d2,
            "measure": measure.value}


def _profit_regions(legs: list[Leg], S: float, T: float, sigma: float,
                    mult: int, target: float) -> tuple[list[float], list[tuple[float, float]]]:
    """Breakevens (P&L crosses ``target``) and the profit intervals above target.

    Piecewise-linear payoff -> exact roots between adjacent strike kinks (and on
    the two outer rays). Returns (breakevens, profit_regions) where each region
    is ``(lo, hi)`` with ``hi == math.inf`` for an open right tail.
    """
    net_cost = net_cost_entry(legs)

    def pnl(s: float) -> float:
        return terminal_payoff(legs, s, net_cost) - target

    strikes = sorted({lg.strike for lg in legs if lg.strike > 0.0})
    # Node grid: 0, strikes, and a far node to capture the right ray's slope.
    far = (strikes[-1] if strikes else S) * 4.0 + S + 1.0
    nodes = [0.0, *strikes, far]
    ys = [pnl(n) for n in nodes]

    breakevens: list[float] = []
    for i in range(len(nodes) - 1):
        x0, x1, y0, y1 = nodes[i], nodes[i + 1], ys[i], ys[i + 1]
        if math.isclose(y0, 0.0, abs_tol=1e-9):
            breakevens.append(x0)
        if (y0 < 0 < y1) or (y0 > 0 > y1):
            # linear interpolation for the crossing
            be = x0 + (x1 - x0) * (0.0 - y0) / (y1 - y0)
            breakevens.append(be)
    # de-dup
    breakevens = sorted({round(b, 10) for b in breakevens})

    # Profit intervals: scan nodes + breakevens, classify midpoints.
    bounds = sorted({0.0, *strikes, *breakevens})
    bounds.append(math.inf)
    regions: list[tuple[float, float]] = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        mid = (lo + 1.0) if hi == math.inf else 0.5 * (lo + hi)
        if pnl(mid) > 0:
            if regions and math.isclose(regions[-1][1], lo, rel_tol=0, abs_tol=1e-9):
                regions[-1] = (regions[-1][0], hi)
            else:
                regions.append((lo, hi))
    return breakevens, regions


def pop_expiry(legs: list[Leg], S: float, T: float, sigma: float, r: float, q: float = 0.0,
               mult: int = 100, target: float = 0.0,
               measure: Measure = Measure.RISK_NEUTRAL, mu: float | None = None) -> dict:
    """Probability of profit at expiry (P&L > ``target``), unioning ALL profit
    intervals over the terminal-payoff curve.

    GOLDEN (bull-call 360/460, BE=398.96): POP = 0.4275 (RN) vs 0.4748 (RW,
    mu=0.10) — proves measure separation (L6).
    """
    drift = resolve_drift(measure, r, q, mu)
    breakevens, regions = _profit_regions(legs, S, T, sigma, mult, target)
    pop = 0.0
    for lo, hi in regions:
        upper = 1.0 if hi == math.inf else prob_price_below(S, hi, T, sigma, drift)
        lower = 0.0 if lo <= 0.0 else prob_price_below(S, lo, T, sigma, drift)
        pop += max(0.0, upper - lower)
    return {"pop": pop, "breakevens": breakevens, "profit_regions": regions,
            "measure": measure.value}


def prob_touch(S: float, H: float, T: float, r: float, sigma: float, q: float = 0.0,
               direction: str = "up",
               measure: Measure = Measure.RISK_NEUTRAL, mu: float | None = None) -> dict:
    """Reiner-Rubinstein one-touch probability of hitting barrier ``H`` before T.

    GOLDENs (RN, S=389.80,T=1,sigma=0.46): touch(460,'up')=0.6840,
    touch(312,'down')=0.6681. Touch >= finish-ITM always.
    """
    drift = resolve_drift(measure, r, q, mu)
    nu = drift - 0.5 * sigma * sigma
    vt = sigma * math.sqrt(T)
    a = math.log(H / S)
    if direction == "up":
        if H <= S:
            return {"prob_touch": 1.0, "direction": direction, "measure": measure.value}
        p = (norm_cdf((-a + nu * T) / vt)
             + math.exp(2.0 * nu * a / (sigma * sigma)) * norm_cdf((-a - nu * T) / vt))
    elif direction == "down":
        if H >= S:
            return {"prob_touch": 1.0, "direction": direction, "measure": measure.value}
        p = (norm_cdf((a - nu * T) / vt)
             + math.exp(2.0 * nu * a / (sigma * sigma)) * norm_cdf((a + nu * T) / vt))
    else:
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    return {"prob_touch": min(1.0, max(0.0, p)), "direction": direction,
            "measure": measure.value}


# ---------------------------------------------------------------------------
# Expected value / EV-style summaries
# ---------------------------------------------------------------------------
def _ev_gross_payoff(legs: list[Leg], S: float, T: float, sigma: float, drift: float,
                     n_nodes: int = 4001) -> float:
    """``E[ gross terminal payoff ]`` (signed intrinsic * mult * qty, NO net_cost)
    under the lognormal terminal law, via a fine trapezoid integral over the
    log-price grid. Accurate for the piecewise-linear option payoff; the closed
    form is cross-checked by MC (L3 / CI rule).

    net_cost is handled by the caller so the t=0 cash flow is discounted
    correctly (it must NOT be re-discounted as if paid at T)."""
    vt = sigma * math.sqrt(T)
    mean = math.log(S) + (drift - 0.5 * sigma * sigma) * T
    # integrate over +-8 std of log-price
    lo, hi = mean - 8.0 * vt, mean + 8.0 * vt
    h = (hi - lo) / (n_nodes - 1)
    inv = 1.0 / (vt * math.sqrt(2.0 * math.pi))
    total = 0.0
    for i in range(n_nodes):
        x = lo + i * h               # x = ln(S_T)
        z = (x - mean) / vt
        dens = inv * math.exp(-0.5 * z * z)
        s_t = math.exp(x)
        gross = terminal_payoff(legs, s_t, 0.0)   # 0.0 net_cost -> pure gross payoff
        w = 0.5 if (i == 0 or i == n_nodes - 1) else 1.0
        total += w * dens * gross
    return total * h


def expected_pnl(legs: list[Leg], S: float, T: float, r: float, sigma: float, q: float = 0.0,
                 measure: Measure = Measure.RISK_NEUTRAL, mu: float | None = None,
                 mult: int = 100) -> dict:
    """Expected terminal P&L (undiscounted and PV) under the chosen measure.

    Under the risk-neutral measure the discounted expected P&L of a *fairly
    priced* spread is ~0 (the MC reconciliation anchor, §5).
    """
    drift = resolve_drift(measure, r, q, mu)
    net_cost = net_cost_entry(legs)            # t=0 cash flow (debit>0 / credit<0)
    e_gross = _ev_gross_payoff(legs, S, T, sigma, drift)   # E[ payoff_T ]
    disc = math.exp(-r * T)
    # PV of P&L: discount the terminal payoff to t=0, net the t=0 cash flow.
    e_pnl_pv = disc * e_gross - net_cost
    # Undiscounted (terminal-dollar) framing: carry the t=0 cash to T.
    e_pnl = e_gross - net_cost / disc
    return {"e_pnl": e_pnl, "e_pnl_pv": e_pnl_pv, "measure": measure.value}


def _payoff_is_monotone(legs: list[Leg], S: float, T: float, sigma: float,
                        mult: int) -> tuple[bool, str]:
    """Detect monotone terminal payoff via the sign of the outer-ray slopes.

    A monotone payoff (e.g. long/short single option, vertical) admits an
    analytic VaR via the lognormal quantile. Non-monotone payoffs (condors,
    flies) set ``method='mc-fallback'`` — VaR/CVaR there come from ``montecarlo``.
    """
    net_cost = net_cost_entry(legs)
    strikes = sorted({lg.strike for lg in legs if lg.strike > 0.0})
    if not strikes:
        return True, "no-strikes"
    far = strikes[-1] * 4.0 + S + 1.0
    grid = [0.0, *strikes, far]
    ys = [terminal_payoff(legs, s, net_cost) for s in grid]
    diffs = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    nonneg = all(d >= -1e-6 for d in diffs)
    nonpos = all(d <= 1e-6 for d in diffs)
    if nonneg:
        return True, "increasing"
    if nonpos:
        return True, "decreasing"
    return False, "non-monotone"


def terminal_risk_summary(legs: list[Leg], S: float, T: float, r: float, sigma: float,
                          q: float = 0.0,
                          measure: Measure = Measure.RISK_NEUTRAL, mu: float | None = None,
                          alphas: tuple[float, ...] = (0.95, 0.99),
                          mult: int = 100) -> dict:
    """Analytic terminal VaR/CVaR for monotone payoffs; flags MC fallback else.

    For a monotone payoff, the loss tail corresponds to a single spot quantile,
    so VaR_alpha is the (signed) loss at the lognormal quantile ``S_q`` and
    CVaR_alpha is the conditional mean loss beyond it. VaR/CVaR reported as
    POSITIVE losses. Non-monotone payoffs return ``method='mc-fallback'`` with
    no analytic numbers (caller routes to ``montecarlo.var_cvar``).
    """
    drift = resolve_drift(measure, r, q, mu)
    monotone, shape = _payoff_is_monotone(legs, S, T, sigma, mult)
    net_cost = net_cost_entry(legs)

    if not monotone:
        return {"method": "mc-fallback", "shape": shape, "measure": measure.value,
                "var": {}, "cvar": {}}

    vt = sigma * math.sqrt(T)
    mean = math.log(S) + (drift - 0.5 * sigma * sigma) * T

    # inverse normal CDF (Acklam) for the spot quantile
    def _norm_ppf(p: float) -> float:
        if p <= 0.0:
            return -math.inf
        if p >= 1.0:
            return math.inf
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00]
        plow, phigh = 0.02425, 1 - 0.02425
        if p < plow:
            qq = math.sqrt(-2 * math.log(p))
            return (((((c[0] * qq + c[1]) * qq + c[2]) * qq + c[3]) * qq + c[4]) * qq + c[5]) / \
                   ((((d[0] * qq + d[1]) * qq + d[2]) * qq + d[3]) * qq + 1)
        if p <= phigh:
            qq = p - 0.5
            rr = qq * qq
            return (((((a[0] * rr + a[1]) * rr + a[2]) * rr + a[3]) * rr + a[4]) * rr + a[5]) * qq / \
                   (((((b[0] * rr + b[1]) * rr + b[2]) * rr + b[3]) * rr + b[4]) * rr + 1)
        qq = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * qq + c[1]) * qq + c[2]) * qq + c[3]) * qq + c[4]) * qq + c[5]) / \
               ((((d[0] * qq + d[1]) * qq + d[2]) * qq + d[3]) * qq + 1)

    increasing = shape == "increasing"
    var_out: dict[str, float] = {}
    cvar_out: dict[str, float] = {}
    for alpha in alphas:
        # Loss tail: increasing payoff -> losses are at LOW spot; decreasing -> high spot.
        tail = 1.0 - alpha
        z = _norm_ppf(tail) if increasing else _norm_ppf(alpha)
        s_q = math.exp(mean + z * vt)
        var_loss = -(terminal_payoff(legs, s_q, net_cost))  # positive loss
        var_out[str(alpha)] = max(0.0, var_loss)
        # CVaR: average loss in the tail via fine quantile integration.
        steps = 200
        acc = 0.0
        for k in range(1, steps + 1):
            u = (k - 0.5) / steps  # 0..1 within the tail
            p_tail = u * tail if increasing else (alpha + u * tail)
            zk = _norm_ppf(p_tail)
            s_k = math.exp(mean + zk * vt)
            acc += -(terminal_payoff(legs, s_k, net_cost))
        cvar_out[str(alpha)] = max(0.0, acc / steps)

    return {"method": "analytic", "shape": shape, "measure": measure.value,
            "var": var_out, "cvar": cvar_out}
