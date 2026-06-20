"""European Black-Scholes-Merton pricing, the full analytic greek surface, and
put-call parity.

This is the single pricing core (design law L1): every European price/greek in
the toolkit flows through ``d1d2`` -> ``price`` here. Puts are first-class — each
right has its own closed form (L2) and is parity-cross-checked. No scipy: the
normal CDF/PDF are implemented from ``math.erf`` / ``math.exp``.

Closed forms (continuous dividend yield ``q``)
----------------------------------------------
    d1 = ( ln(S/K) + (r - q + sigma**2 / 2) * T ) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)

    Call = S * e^(-qT) * N(d1) - K * e^(-rT) * N(d2)
    Put  = K * e^(-rT) * N(-d2) - S * e^(-qT) * N(-d1)

First-order greeks (raw, per unit of the bumped variable)
    Delta_call =  e^(-qT) * N(d1)
    Delta_put  = -e^(-qT) * N(-d1)
    Gamma      =  e^(-qT) * n(d1) / (S * sigma * sqrt(T))
    Vega       =  S * e^(-qT) * n(d1) * sqrt(T)            (per 1.0 vol, i.e. 100 vol pts)
    Theta_call = -S e^(-qT) n(d1) sigma /(2 sqrt(T)) - r K e^(-rT) N(d2)  + q S e^(-qT) N(d1)
    Theta_put  = -S e^(-qT) n(d1) sigma /(2 sqrt(T)) + r K e^(-rT) N(-d2) - q S e^(-qT) N(-d1)
    Rho_call   =  K T e^(-rT) N(d2)                        (per 1.0 in r, i.e. 100 %)
    Rho_put    = -K T e^(-rT) N(-d2)

Trader-unit greeks (what ``first_order`` / ``all_greeks`` report, per L5):
    theta_day = theta_yr / 365      (per calendar day)
    vega      = vega_raw / 100      (per 1 vol POINT)
    rho       = rho_raw / 100       (per 1 PERCENT)

Higher-order greeks
    Vanna  =  d Delta / d sigma  = -e^(-qT) n(d1) d2 / sigma
    Vomma  =  d Vega  / d sigma  =  Vega_raw * d1 * d2 / sigma
    Veta   = -d Vega  / d t      (calendar-time decay of vega)
    Ultima =  d Vomma / d sigma  = (-Vega_raw / sigma**2)(d1 d2 (1 - d1 d2) + d1**2 + d2**2)
    Charm  = -d Delta / d t      (calendar-time decay of delta)
    Speed  =  d Gamma / d S      = -(Gamma / S)(d1 /(sigma sqrt(T)) + 1)
    Color  = -d Gamma / d t      (calendar-time decay of gamma)
    Zomma  =  d Gamma / d sigma  =  Gamma * (d1 d2 - 1) / sigma

All time-decay greeks (charm/color/veta) use the CALENDAR-TIME convention
(d/dt = -d/dT) so they cross-check against the finite-difference harness in
``american.fd_greek`` with consistent signs. ``*_raw`` forms are the per-unit
derivatives before trader-unit scaling; ``charm_day`` / ``color_day`` are the
per-calendar-day forms.

References:
  - Hull, *Options, Futures, and Other Derivatives*, ch. 19 (Greek letters).
  - Haug, *The Complete Guide to Option Pricing Formulas*, 2nd ed. (higher-order greeks).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Value objects (local, signature-compatible with bursahack.options.types).
#
# The shared contract places Right / D1D2 / Greeks in `types.py`. That module is
# owned by another build agent. To keep this pricing core self-contained and
# independently testable, equivalent containers are defined here with IDENTICAL
# field names/values. When `types.py` lands its versions are drop-in compatible.
# ---------------------------------------------------------------------------


class Right(str, Enum):
    """Option right / instrument kind. YAML and callers use the VALUE."""

    CALL = "C"
    PUT = "P"
    STOCK = "S"
    CASH = "X"


@dataclass(frozen=True)
class D1D2:
    """d1/d2 plus the normal evaluations every BSM caller needs."""

    d1: float
    d2: float
    nd1: float  # n(d1) = norm_pdf(d1)
    Nd1: float  # N(d1)
    Nd2: float  # N(d2)


@dataclass(frozen=True)
class Greeks:
    """Full analytic greek surface in trader units.

    theta in per-calendar-day (``theta_day``) and per-year (``theta_yr``);
    vega per 1 vol point; rho per 1 percent. Higher-order fields are populated
    by ``all_greeks`` here, and may be ``None`` when a tree pricer cannot fill
    them analytically (see ``american.crr_greeks``).
    """

    delta: float
    gamma: float
    theta_day: float
    theta_yr: float
    vega: float  # per 1 vol point
    rho: float  # per 1 percent
    vanna: float | None = None  # per vol point
    vomma: float | None = None  # per vol point^2
    veta: float | None = None
    ultima: float | None = None
    charm_day: float | None = None
    speed: float | None = None
    color_day: float | None = None
    zomma: float | None = None  # per vol point


# ---------------------------------------------------------------------------
# Normal distribution primitives (no scipy)
# ---------------------------------------------------------------------------


def norm_pdf(x: float) -> float:
    """Standard-normal PDF n(x) = exp(-x^2/2) / sqrt(2*pi)."""
    return math.exp(-x * x / 2.0) / (2.0 * math.pi) ** 0.5


def norm_cdf(x: float) -> float:
    """Standard-normal CDF N(x) = 0.5 * (1 + erf(x / sqrt(2)))."""
    return 0.5 * (1.0 + math.erf(x / 2.0**0.5))


# ---------------------------------------------------------------------------
# d1 / d2
# ---------------------------------------------------------------------------


def d1d2(S: float, K: float, T: float, r: float, q: float, sigma: float) -> D1D2:
    """Shared d1/d2 + n(d1), N(d1), N(d2).

    Guards the total-vol -> 0 limit (sigma*sqrt(T) -> 0): instead of dividing by
    zero, return the deterministic forward-vs-strike step limit so callers
    degrade to intrinsic, never NaN. With forward F = S*e^((r-q)T):
        F > K -> d1, d2 -> +inf -> Nd1 = Nd2 = 1
        F < K -> d1, d2 -> -inf -> Nd1 = Nd2 = 0
        F = K -> d1, d2 -> 0      -> Nd1 = Nd2 = 0.5
    """
    if S <= 0.0 or K <= 0.0:
        raise ValueError(f"S and K must be positive (got S={S}, K={K})")
    total_vol = sigma * math.sqrt(T) if T > 0.0 else 0.0
    if total_vol <= 1e-300:
        fwd = S * math.exp((r - q) * T)
        if fwd > K:
            d1 = d2 = math.inf
            Nd1 = Nd2 = 1.0
        elif fwd < K:
            d1 = d2 = -math.inf
            Nd1 = Nd2 = 0.0
        else:
            d1 = d2 = 0.0
            Nd1 = Nd2 = 0.5
        nd1 = 0.0
        return D1D2(d1=d1, d2=d2, nd1=nd1, Nd1=Nd1, Nd2=Nd2)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / total_vol
    d2 = d1 - total_vol
    return D1D2(d1=d1, d2=d2, nd1=norm_pdf(d1), Nd1=norm_cdf(d1), Nd2=norm_cdf(d2))


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------


def price(S: float, K: float, T: float, r: float, q: float, sigma: float, right: Right) -> float:
    """European BSM price with continuous yield q. Put has its OWN closed form (L2).

    At T <= 0 returns discounted intrinsic-at-forward (the deterministic limit).
    """
    right = Right(right)
    if right not in (Right.CALL, Right.PUT):
        raise ValueError(f"price() expects CALL or PUT, got {right!r}")
    dd = d1d2(S, K, T, r, q, sigma)
    df_r = math.exp(-r * T)
    df_q = math.exp(-q * T)
    if right is Right.CALL:
        return S * df_q * dd.Nd1 - K * df_r * dd.Nd2
    # put: N(-d1) = 1 - N(d1), N(-d2) = 1 - N(d2)
    return K * df_r * (1.0 - dd.Nd2) - S * df_q * (1.0 - dd.Nd1)


# ---------------------------------------------------------------------------
# First-order greeks
# ---------------------------------------------------------------------------


def first_order(S: float, K: float, T: float, r: float, q: float, sigma: float,
                right: Right) -> dict[str, float]:
    """First-order greeks in TRADER units.

    keys: delta, gamma, theta_day, theta_yr, vega (per vol pt), rho (per 1%).
    """
    right = Right(right)
    dd = d1d2(S, K, T, r, q, sigma)
    sqrtT = math.sqrt(T)
    df_r = math.exp(-r * T)
    df_q = math.exp(-q * T)

    gamma = df_q * dd.nd1 / (S * sigma * sqrtT)
    vega_raw = S * df_q * dd.nd1 * sqrtT  # per 1.0 vol

    if right is Right.CALL:
        delta = df_q * dd.Nd1
        theta_yr = (
            -S * df_q * dd.nd1 * sigma / (2.0 * sqrtT)
            - r * K * df_r * dd.Nd2
            + q * S * df_q * dd.Nd1
        )
        rho_raw = K * T * df_r * dd.Nd2  # per 1.0 in r
    elif right is Right.PUT:
        delta = -df_q * (1.0 - dd.Nd1)
        theta_yr = (
            -S * df_q * dd.nd1 * sigma / (2.0 * sqrtT)
            + r * K * df_r * (1.0 - dd.Nd2)
            - q * S * df_q * (1.0 - dd.Nd1)
        )
        rho_raw = -K * T * df_r * (1.0 - dd.Nd2)
    else:
        raise ValueError(f"first_order() expects CALL or PUT, got {right!r}")

    return {
        "delta": delta,
        "gamma": gamma,
        "theta_day": theta_yr / 365.0,
        "theta_yr": theta_yr,
        "vega": vega_raw / 100.0,  # per 1 vol POINT
        "rho": rho_raw / 100.0,  # per 1 PERCENT
    }


# ---------------------------------------------------------------------------
# Vol greeks (vanna / vomma / veta / ultima)
# ---------------------------------------------------------------------------


def vol_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float) -> dict[str, float]:
    """Vol-related higher-order greeks (right-independent — these are the same
    for calls and puts).

    keys: vanna (per vol pt), vomma (per vol pt^2), veta, ultima, plus *_raw
    forms (per unit vol / per year, before trader-unit scaling).
    Veta uses the calendar-time-decay convention (veta = -dVega/dT).
    """
    dd = d1d2(S, K, T, r, q, sigma)
    sqrtT = math.sqrt(T)
    df_q = math.exp(-q * T)
    d1, d2 = dd.d1, dd.d2
    vega_raw = S * df_q * dd.nd1 * sqrtT

    # vanna = d delta / d sigma  (per 1.0 vol)
    vanna_raw = -df_q * dd.nd1 * d2 / sigma
    # vomma = d vega / d sigma   (per 1.0 vol^2)
    vomma_raw = vega_raw * d1 * d2 / sigma
    # ultima = d vomma / d sigma (per 1.0 vol^3)
    ultima_raw = (-vega_raw / (sigma * sigma)) * (
        d1 * d2 * (1.0 - d1 * d2) + d1 * d1 + d2 * d2
    )
    # veta_inner = dVega/dT ; calendar-time decay veta = -dVega/dT
    veta_dT = -S * df_q * dd.nd1 * sqrtT * (
        q + (r - q) * d1 / (sigma * sqrtT) - (1.0 + d1 * d2) / (2.0 * T)
    )
    veta_raw = -veta_dT  # calendar-time-decay convention (per year)

    return {
        "vanna": vanna_raw / 100.0,  # per vol POINT
        "vomma": vomma_raw / (100.0 * 100.0),  # per vol POINT^2
        "veta": veta_raw / 100.0,  # decay per year, per vol point
        "ultima": ultima_raw / (100.0**3),
        "vanna_raw": vanna_raw,
        "vomma_raw": vomma_raw,
        "veta_raw": veta_raw,
        "ultima_raw": ultima_raw,
    }


# ---------------------------------------------------------------------------
# Spot/time greeks (charm / speed / color / zomma)
# ---------------------------------------------------------------------------


def spot_time_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float,
                     right: Right) -> dict[str, float]:
    """Spot- and time-related higher-order greeks.

    keys: charm_day, speed, color_day, zomma (per vol pt), plus *_raw forms.
    Charm and color use the calendar-time-decay convention
    (charm = -dDelta/dT, color = -dGamma/dT); *_day forms divide the per-year
    raw by 365. Speed and zomma are pure spot/vol derivatives (no time scaling).
    """
    right = Right(right)
    dd = d1d2(S, K, T, r, q, sigma)
    sqrtT = math.sqrt(T)
    df_q = math.exp(-q * T)
    d1, d2 = dd.d1, dd.d2
    gamma = df_q * dd.nd1 / (S * sigma * sqrtT)

    # charm = -dDelta/dt (calendar-time decay, per YEAR)
    common = df_q * dd.nd1 * (2.0 * (r - q) * T - d2 * sigma * sqrtT) / (2.0 * T * sigma * sqrtT)
    if right is Right.CALL:
        charm_raw = q * df_q * dd.Nd1 - common
    elif right is Right.PUT:
        charm_raw = -q * df_q * (1.0 - dd.Nd1) - common
    else:
        raise ValueError(f"spot_time_greeks() expects CALL or PUT, got {right!r}")

    # speed = dGamma/dS
    speed_raw = -(gamma / S) * (d1 / (sigma * sqrtT) + 1.0)

    # color = -dGamma/dt (calendar-time decay, per YEAR)
    color_dT = -df_q * dd.nd1 / (2.0 * S * T * sigma * sqrtT) * (
        2.0 * q * T + 1.0 + (2.0 * (r - q) * T - d2 * sigma * sqrtT) / (sigma * sqrtT) * d1
    )
    color_raw = -color_dT

    # zomma = dGamma/dsigma
    zomma_raw = gamma * (d1 * d2 - 1.0) / sigma

    return {
        "charm_day": charm_raw / 365.0,
        "speed": speed_raw,
        "color_day": color_raw / 365.0,
        "zomma": zomma_raw / 100.0,  # per vol POINT
        "charm_raw": charm_raw,  # per year
        "speed_raw": speed_raw,
        "color_raw": color_raw,  # per year
        "zomma_raw": zomma_raw,
    }


# ---------------------------------------------------------------------------
# Full analytic surface
# ---------------------------------------------------------------------------


def all_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float,
               right: Right) -> Greeks:
    """Full analytic greek surface in trader units (theta/day, vega/vol pt, rho/1%)."""
    fo = first_order(S, K, T, r, q, sigma, right)
    vg = vol_greeks(S, K, T, r, q, sigma)
    stg = spot_time_greeks(S, K, T, r, q, sigma, right)
    return Greeks(
        delta=fo["delta"],
        gamma=fo["gamma"],
        theta_day=fo["theta_day"],
        theta_yr=fo["theta_yr"],
        vega=fo["vega"],
        rho=fo["rho"],
        vanna=vg["vanna"],
        vomma=vg["vomma"],
        veta=vg["veta"],
        ultima=vg["ultima"],
        charm_day=stg["charm_day"],
        speed=stg["speed"],
        color_day=stg["color_day"],
        zomma=stg["zomma"],
    )


# ---------------------------------------------------------------------------
# Put-call parity
# ---------------------------------------------------------------------------


def parity_residual(C: float, P: float, S: float, K: float, T: float, r: float, q: float) -> float:
    """Put-call parity residual: (C - P) - (S*e^(-qT) - K*e^(-rT)).

    ~0 when call and put prices are mutually consistent. Used as a hard
    cross-check on every priced pair (L2).
    """
    return (C - P) - (S * math.exp(-q * T) - K * math.exp(-r * T))


def implied_forward(S: float, T: float, r: float, q: float) -> float:
    """Forward price F = S * e^((r-q)T). The drift carrier for parity/IV."""
    return S * math.exp((r - q) * T)
