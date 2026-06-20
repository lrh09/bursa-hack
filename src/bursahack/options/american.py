"""American (and European-limit) option pricing via the Cox-Ross-Rubinstein
binomial tree, with both continuous-yield (q) and DISCRETE cash dividends, plus
a finite-difference greek harness used to cross-check the analytic surface in
``bs.py``.

This routes the American / discrete-dividend cases of design law L1 (one pricing
core): European-style names go through ``bs.price``; early-exercise and
discrete-cash-dividend names go through the tree here. The European limit of
``crr_price`` converges to ``bs.price``.

CRR tree
--------
With N steps and dt = T/N:
    u = exp(sigma * sqrt(dt)),  d = 1/u
    a = exp((r - q) * dt)                      (continuous-yield growth factor)
    p = (a - d) / (u - d)                       (risk-neutral up-probability)
    discount per step = exp(-r * dt)
Backward induction; at each node the AMERICAN value is max(continuation,
intrinsic). The EUROPEAN value omits the intrinsic comparison.

Discrete dividends (escrowed-/PV-subtraction model)
---------------------------------------------------
The classic Hull approach: build the tree on the dividend-stripped spot
S0 = S - PV(future dividends). At any node at time t the ACTUAL stock price used
for the early-exercise intrinsic check is the tree value PLUS the present value
of dividends still to be paid after t. q MUST be 0 when a discrete schedule is
supplied (a name pays continuous OR discrete dividends, never both).

References:
  - Cox, Ross, Rubinstein (1979), "Option Pricing: A Simplified Approach".
  - Hull, *Options, Futures, and Other Derivatives*, ch. 13 & 21 (binomial trees,
    options on dividend-paying stocks).
"""
from __future__ import annotations

import math
from typing import Callable

from bursahack.options.bs import Greeks, Right

__all__ = [
    "Style",
    "crr_price",
    "crr_greeks",
    "early_exercise_boundary",
    "pv_dividends",
    "price_discrete_div",
    "fd_greek",
    "cross_check",
]


# ---------------------------------------------------------------------------
# Style enum (local, signature-compatible with bursahack.options.types.Style)
# ---------------------------------------------------------------------------
from enum import Enum


class Style(str, Enum):
    EUROPEAN = "european"
    AMERICAN = "american"


def _intrinsic(S: float, K: float, right: Right) -> float:
    return max(S - K, 0.0) if right is Right.CALL else max(K - S, 0.0)


# ---------------------------------------------------------------------------
# CRR price (continuous yield)
# ---------------------------------------------------------------------------


def crr_price(S: float, K: float, T: float, r: float, q: float, sigma: float,
              right: Right, style: Style, N: int = 400) -> float:
    """Cox-Ross-Rubinstein price with an early-exercise check at every node.

    Converges to ``bs.price`` in the European limit. ``style`` selects whether
    the early-exercise comparison is applied.
    """
    right = Right(right)
    style = Style(style)
    if right not in (Right.CALL, Right.PUT):
        raise ValueError(f"crr_price() expects CALL or PUT, got {right!r}")
    if N < 1:
        raise ValueError("N must be >= 1")
    if T <= 0.0:
        return _intrinsic(S, K, right)
    if sigma <= 0.0:
        # Deterministic forward limit -> discounted intrinsic at the forward.
        fwd = S * math.exp((r - q) * T)
        euro = math.exp(-r * T) * _intrinsic(fwd, K, right)
        if style is Style.EUROPEAN:
            return euro
        return max(euro, _intrinsic(S, K, right))

    dt = T / N
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    a = math.exp((r - q) * dt)
    p = (a - d) / (u - d)
    disc = math.exp(-r * dt)
    is_amer = style is Style.AMERICAN

    # Terminal layer.
    vals = [_intrinsic(S * (u**j) * (d ** (N - j)), K, right) for j in range(N + 1)]

    for i in range(N - 1, -1, -1):
        new_vals = [0.0] * (i + 1)
        for j in range(i + 1):
            cont = disc * (p * vals[j + 1] + (1.0 - p) * vals[j])
            if is_amer:
                node_S = S * (u**j) * (d ** (i - j))
                cont = max(cont, _intrinsic(node_S, K, right))
            new_vals[j] = cont
        vals = new_vals
    return vals[0]


# ---------------------------------------------------------------------------
# Discrete dividends
# ---------------------------------------------------------------------------


def pv_dividends(schedule: tuple[tuple[float, float], ...], r: float) -> float:
    """Present value of a discrete cash-dividend schedule.

    schedule = ((t_years, cash_amt), ...). Returns sum(amt_i * exp(-r * t_i)).
    """
    return sum(amt * math.exp(-r * t) for (t, amt) in schedule)


def _crr_price_discrete_div(S: float, K: float, T: float, r: float, sigma: float,
                            right: Right, style: Style,
                            schedule: tuple[tuple[float, float], ...], N: int) -> float:
    """CRR on the dividend-stripped spot; intrinsic checks add back PV of future
    dividends so early exercise sees the true stock price (q is implicitly 0)."""
    right = Right(right)
    style = Style(style)
    if T <= 0.0:
        return _intrinsic(S, K, right)
    if sigma <= 0.0:
        # Forward already net of dividends (r drift, divs paid out).
        fwd = (S - pv_dividends(schedule, r)) * math.exp(r * T)
        euro = math.exp(-r * T) * _intrinsic(fwd, K, right)
        if style is Style.EUROPEAN:
            return euro
        return max(euro, _intrinsic(S, K, right))

    dt = T / N
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    a = math.exp(r * dt)  # q = 0 for a discrete-dividend name
    p = (a - d) / (u - d)
    disc = math.exp(-r * dt)
    is_amer = style is Style.AMERICAN

    def pv_future(t: float) -> float:
        return sum(amt * math.exp(-r * (td - t)) for (td, amt) in schedule if td > t + 1e-12)

    S0 = S - pv_dividends(schedule, r)  # dividend-stripped spot
    if S0 <= 0.0:
        raise ValueError("PV of dividends exceeds spot; degenerate schedule")

    # Terminal layer (no future dividends at T).
    vals = [_intrinsic(S0 * (u**j) * (d ** (N - j)), K, right) for j in range(N + 1)]

    for i in range(N - 1, -1, -1):
        t = i * dt
        pvf = pv_future(t)
        new_vals = [0.0] * (i + 1)
        for j in range(i + 1):
            cont = disc * (p * vals[j + 1] + (1.0 - p) * vals[j])
            if is_amer:
                node_S = S0 * (u**j) * (d ** (i - j)) + pvf  # add back future-div PV
                cont = max(cont, _intrinsic(node_S, K, right))
            new_vals[j] = cont
        vals = new_vals
    return vals[0]


def price_discrete_div(S: float, K: float, T: float, r: float, q: float, sigma: float,
                       right: Right, style: Style,
                       schedule: tuple[tuple[float, float], ...],
                       method: str = "escrowed", N: int = 400) -> tuple[float, str]:
    """Discrete-dividend pricing.

    ``q`` is accepted for signature consistency but MUST be 0.0 when ``schedule``
    is non-empty (a name has discrete OR continuous dividends, never both) — this
    is asserted, raising ValueError otherwise.

    method='escrowed': European, priced via BSM on S' = S - pv_dividends(schedule, r).
    method='crr':      American, CRR subtracting cash at ex-div nodes.

    Returns (price, method_used).
    """
    right = Right(right)
    style = Style(style)
    if schedule and q != 0.0:
        raise ValueError(
            "q must be 0.0 for a discrete-dividend name "
            f"(got q={q}); a name has discrete OR continuous divs, never both"
        )

    if not schedule:
        # No dividends -> defer to the standard tree (keeps the contract total).
        return crr_price(S, K, T, r, q, sigma, right, style, N=N), method

    if method == "escrowed":
        # Lazy import to avoid any import-order coupling; bs has no heavy deps.
        from bursahack.options.bs import price as bs_price

        s_adj = S - pv_dividends(schedule, r)
        if s_adj <= 0.0:
            raise ValueError("PV of dividends exceeds spot; degenerate schedule")
        return bs_price(s_adj, K, T, r, 0.0, sigma, right), "escrowed"

    if method == "crr":
        return (
            _crr_price_discrete_div(S, K, T, r, sigma, right, style, schedule, N),
            "crr",
        )

    raise ValueError(f"method must be 'escrowed' or 'crr', got {method!r}")


# ---------------------------------------------------------------------------
# Finite-difference greek harness
# ---------------------------------------------------------------------------

_FD_PARAM_KEYS = {"S", "K", "T", "r", "q", "sigma", "right", "style", "N"}


def fd_greek(pricer: Callable[..., float], params: dict, greek: str,
             h: float | None = None) -> float:
    """Central-difference greek by bumping one input and repricing through ``pricer``.

    params dict schema (FROZEN keys): {'S','K','T','r','q','sigma','right'} and,
    when ``pricer`` is a tree, optionally 'style','N'. ``pricer(**params) -> float``.

    greek in {'delta','gamma','vega','theta','rho','vanna','vomma','charm',
    'speed','zomma','color','veta','ultima'}.

    Default steps: dS = 1e-2 * S, dSigma = 1e-4, dT = 1/365, dr = 1e-4.
    theta/charm/color/veta returned PER YEAR using the CALENDAR-TIME-DECAY
    convention (d/dt = -d/dT) so they match the analytic surface signs.
    Works for any pricer (European or American).
    """
    bad = set(params) - _FD_PARAM_KEYS
    if bad:
        raise ValueError(f"unexpected param keys: {sorted(bad)}")
    p0 = dict(params)
    S = float(p0["S"])
    sigma = float(p0["sigma"])

    def reprice(**over: float) -> float:
        pp = dict(p0)
        pp.update(over)
        return pricer(**pp)

    def first(var: str, step: float) -> float:
        base = float(p0[var])
        return (reprice(**{var: base + step}) - reprice(**{var: base - step})) / (2.0 * step)

    def second(var: str, step: float) -> float:
        base = float(p0[var])
        return (
            reprice(**{var: base + step}) - 2.0 * reprice(**p0) + reprice(**{var: base - step})
        ) / (step * step)

    def cross(var1: str, step1: float, var2: str, step2: float) -> float:
        b1, b2 = float(p0[var1]), float(p0[var2])
        fpp = reprice(**{var1: b1 + step1, var2: b2 + step2})
        fpm = reprice(**{var1: b1 + step1, var2: b2 - step2})
        fmp = reprice(**{var1: b1 - step1, var2: b2 + step2})
        fmm = reprice(**{var1: b1 - step1, var2: b2 - step2})
        return (fpp - fpm - fmp + fmm) / (4.0 * step1 * step2)

    dS = h if h is not None else 1e-2 * S
    dSig = 1e-4
    dT = 1.0 / 365.0
    dr = 1e-4

    g = greek.lower()
    if g == "delta":
        return first("S", dS)
    if g == "gamma":
        return second("S", dS)
    if g == "vega":
        # raw per 1.0 vol (analytic vega_raw); callers divide by 100 for vol-pt.
        return first("sigma", dSig)
    if g == "rho":
        return first("r", dr)  # raw per 1.0 in r
    if g == "theta":
        # calendar-time decay per year: -dPrice/dT
        return -first("T", dT)
    if g == "vanna":
        return cross("S", dS, "sigma", dSig)  # d^2 Price / dS dsigma == dDelta/dsigma
    if g == "vomma":
        return second("sigma", dSig)
    if g == "charm":
        # -d(delta)/dt = -d/dT (dPrice/dS) ; use mixed S,T second difference, negated
        return -cross("S", dS, "T", dT)
    if g == "speed":
        # d^3 Price / dS^3 (third central difference)
        base = S
        f_2u = reprice(S=base + 2 * dS)
        f_u = reprice(S=base + dS)
        f_d = reprice(S=base - dS)
        f_2d = reprice(S=base - 2 * dS)
        return (f_2u - 2 * f_u + 2 * f_d - f_2d) / (2.0 * dS**3)
    if g == "zomma":
        # d(gamma)/dsigma : (gamma(sig+)-gamma(sig-))/(2 dSig)
        base_sig = sigma
        gp = second_at(reprice, p0, "S", dS, "sigma", base_sig + dSig)
        gm = second_at(reprice, p0, "S", dS, "sigma", base_sig - dSig)
        return (gp - gm) / (2.0 * dSig)
    if g == "color":
        # -d(gamma)/dt = -d/dT (gamma)
        gp = second_at(reprice, p0, "S", dS, "T", float(p0["T"]) + dT)
        gm = second_at(reprice, p0, "S", dS, "T", float(p0["T"]) - dT)
        return -(gp - gm) / (2.0 * dT)
    if g == "veta":
        # -d(vega)/dt = -d/dT (dPrice/dsigma)
        return -cross("sigma", dSig, "T", dT)
    if g == "ultima":
        # d(vomma)/dsigma = third sigma derivative
        base = sigma
        f_2u = reprice(sigma=base + 2 * dSig)
        f_u = reprice(sigma=base + dSig)
        f_d = reprice(sigma=base - dSig)
        f_2d = reprice(sigma=base - 2 * dSig)
        return (f_2u - 2 * f_u + 2 * f_d - f_2d) / (2.0 * dSig**3)
    raise ValueError(f"unknown greek {greek!r}")


def second_at(reprice: Callable[..., float], p0: dict, var: str, step: float,
              other_var: str, other_val: float) -> float:
    """Helper: second central difference in ``var`` while holding ``other_var`` at
    a shifted value. Used to evaluate gamma at a bumped sigma/T for zomma/color."""
    base = float(p0[var])

    def f(x: float) -> float:
        pp = dict(p0)
        pp[var] = x
        pp[other_var] = other_val
        return reprice(**pp)

    return (f(base + step) - 2.0 * f(base) + f(base - step)) / (step * step)


def cross_check(analytic: float, numeric: float, tol: float = 1e-4) -> tuple[bool, float]:
    """Returns (within_tol, abs_gap). The CI assertion helper.

    Uses an absolute-OR-relative tolerance so it behaves on both tiny greeks
    (speed ~ 1e-6) and large ones (vega ~ 100).
    """
    gap = abs(analytic - numeric)
    scale = max(1.0, abs(analytic), abs(numeric))
    return (gap <= tol * scale, gap)


# ---------------------------------------------------------------------------
# Tree greeks
# ---------------------------------------------------------------------------


def crr_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float,
               right: Right, style: Style, N: int = 400) -> Greeks:
    """delta & gamma from tree nodes; theta from the tree; ALL higher-order greeks
    via ``fd_greek`` over ``crr_price``. Fields not computed remain None per the
    Greeks contract.

    Delta/gamma/theta are read directly from the first tree layers (no extra
    repricings); the rest bump ``crr_price`` through the finite-difference harness.
    """
    right = Right(right)
    style = Style(style)
    delta_t, gamma_t, theta_yr_t = _tree_dgt(S, K, T, r, q, sigma, right, style, N)

    params = {
        "S": S, "K": K, "T": T, "r": r, "q": q, "sigma": sigma,
        "right": right, "style": style, "N": N,
    }

    def pricer(**pp: float) -> float:
        return crr_price(
            pp["S"], pp["K"], pp["T"], pp["r"], pp["q"], pp["sigma"],
            pp["right"], pp["style"], N=int(pp["N"]),
        )

    vega_raw = fd_greek(pricer, params, "vega")
    rho_raw = fd_greek(pricer, params, "rho")
    vanna_raw = fd_greek(pricer, params, "vanna")
    vomma_raw = fd_greek(pricer, params, "vomma")
    charm_raw = fd_greek(pricer, params, "charm")  # per year
    speed_raw = fd_greek(pricer, params, "speed")
    color_raw = fd_greek(pricer, params, "color")  # per year
    zomma_raw = fd_greek(pricer, params, "zomma")
    veta_raw = fd_greek(pricer, params, "veta")  # per year
    ultima_raw = fd_greek(pricer, params, "ultima")

    return Greeks(
        delta=delta_t,
        gamma=gamma_t,
        theta_day=theta_yr_t / 365.0,
        theta_yr=theta_yr_t,
        vega=vega_raw / 100.0,
        rho=rho_raw / 100.0,
        vanna=vanna_raw / 100.0,
        vomma=vomma_raw / (100.0 * 100.0),
        veta=veta_raw / 100.0,
        ultima=ultima_raw / (100.0**3),
        charm_day=charm_raw / 365.0,
        speed=speed_raw,
        color_day=color_raw / 365.0,
        zomma=zomma_raw / 100.0,
    )


def _tree_dgt(S: float, K: float, T: float, r: float, q: float, sigma: float,
              right: Right, style: Style, N: int) -> tuple[float, float, float]:
    """Read delta, gamma, theta (per year) directly off the lowest tree layers.

    Builds the tree once and keeps the first three time-slices so the standard
    central-node estimates can be formed without extra repricings.
    """
    if T <= 0.0 or sigma <= 0.0:
        # Degenerate: fall back to finite differences on crr_price.
        params = {"S": S, "K": K, "T": T, "r": r, "q": q, "sigma": sigma,
                  "right": right, "style": style, "N": N}

        def pricer(**pp: float) -> float:
            return crr_price(pp["S"], pp["K"], pp["T"], pp["r"], pp["q"], pp["sigma"],
                             pp["right"], pp["style"], N=int(pp["N"]))

        return (fd_greek(pricer, params, "delta"),
                fd_greek(pricer, params, "gamma"),
                -fd_greek(pricer, params, "theta") * -1.0)

    dt = T / N
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    a = math.exp((r - q) * dt)
    p = (a - d) / (u - d)
    disc = math.exp(-r * dt)
    is_amer = style is Style.AMERICAN

    vals = [_intrinsic(S * (u**j) * (d ** (N - j)), K, right) for j in range(N + 1)]
    layer2: list[float] = []  # values at step 2
    layer1: list[float] = []  # values at step 1
    layer0 = 0.0

    for i in range(N - 1, -1, -1):
        new_vals = [0.0] * (i + 1)
        for j in range(i + 1):
            cont = disc * (p * vals[j + 1] + (1.0 - p) * vals[j])
            if is_amer:
                node_S = S * (u**j) * (d ** (i - j))
                cont = max(cont, _intrinsic(node_S, K, right))
            new_vals[j] = cont
        vals = new_vals
        if i == 2:
            layer2 = list(vals)
        elif i == 1:
            layer1 = list(vals)
        elif i == 0:
            layer0 = vals[0]

    # Spot prices at step 2: S*u^2, S, S*d^2 (j=2,1,0).
    s_uu = S * u * u
    s_dd = S * d * d
    v_uu, v_md, v_dd = layer2[2], layer2[1], layer2[0]
    delta = (v_uu - v_dd) / (s_uu - s_dd)
    h_up = s_uu - S
    h_dn = S - s_dd
    gamma = ((v_uu - v_md) / h_up - (v_md - v_dd) / h_dn) / (0.5 * (h_up + h_dn))
    # theta: central node at step 2 (value v_md, time 2*dt) vs node at step 0
    # standard CRR theta ~ (v_md - v0) / (2*dt) ; per year already.
    theta_yr = (v_md - layer0) / (2.0 * dt)
    return delta, gamma, theta_yr


# ---------------------------------------------------------------------------
# Early-exercise boundary
# ---------------------------------------------------------------------------


def early_exercise_boundary(S: float, K: float, T: float, r: float, q: float, sigma: float,
                            right: Right, N: int = 400) -> list[tuple[float, float]]:
    """Critical-price boundary [(t, S*)] for an American option.

    At each time slice, S* is the boundary stock price where continuation value
    first crosses intrinsic value (the holder is indifferent). Returns the
    (time, critical_price) pairs from t=0 outward; slices with no exercise region
    are omitted.
    """
    right = Right(right)
    if T <= 0.0 or sigma <= 0.0 or N < 1:
        return []
    dt = T / N
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    a = math.exp((r - q) * dt)
    p = (a - d) / (u - d)
    disc = math.exp(-r * dt)

    vals = [_intrinsic(S * (u**j) * (d ** (N - j)), K, right) for j in range(N + 1)]
    boundary: list[tuple[float, float]] = []

    for i in range(N - 1, -1, -1):
        t = i * dt
        new_vals = [0.0] * (i + 1)
        crit: float | None = None
        for j in range(i + 1):
            cont = disc * (p * vals[j + 1] + (1.0 - p) * vals[j])
            node_S = S * (u**j) * (d ** (i - j))
            intr = _intrinsic(node_S, K, right)
            if intr >= cont and intr > 0.0:
                # exercise region; for a call it's the lowest exercised price,
                # for a put the highest exercised price.
                if right is Right.CALL:
                    if crit is None or node_S < crit:
                        crit = node_S
                else:
                    if crit is None or node_S > crit:
                        crit = node_S
                new_vals[j] = intr
            else:
                new_vals[j] = cont
        vals = new_vals
        if crit is not None:
            boundary.append((t, crit))

    boundary.reverse()  # t ascending
    return boundary
