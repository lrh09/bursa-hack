"""Implied-volatility solver: Newton (vega step) with a bisection fallback.

Inverts a European option price back to the Black-Scholes volatility that
reproduces it. Two guards make the solver robust and *deterministic*:

  1. A price-bounds pre-check rejects quotes below intrinsic or above the
     no-arbitrage ceiling BEFORE any iteration runs.
  2. Newton is seeded with the Brenner-Subrahmanyam ATM closed form and falls
     back to bisection whenever vega collapses (deep-OTM), a Newton step leaves
     the bracket, or the residual diverges.

The solver works in the FORWARD measure so a call and its parity-paired put
solve to the SAME implied vol:

    F  = S * exp((r - q) * T)                       forward price
    c  = exp(-r*T) * [ F*N(d1) - K*N(d2) ]          (Black-76 form of BSM call)
    d1 = ( ln(F/K) + 0.5*sigma^2*T ) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)

Brenner-Subrahmanyam ATM seed (good first guess near the money):

    sigma_0 ~ sqrt(2*pi / T) * price / S

No-arbitrage bounds (European, continuous yield q):

    call:  max(S*e^{-qT} - K*e^{-rT}, 0)  <=  C  <=  S*e^{-qT}
    put :  max(K*e^{-rT} - S*e^{-qT}, 0)  <=  P  <=  K*e^{-rT}

"deep-OTM" -- the condition that forces method == 'bisection' -- is defined as
vega < 1e-6 at the Black-Scholes seed. That is a deterministic, testable trigger.

References:
  - Brenner & Subrahmanyam (1988), "A Simple Formula to Compute the Implied
    Standard Deviation", Financial Analysts Journal.
  - Hull, "Options, Futures, and Other Derivatives" -- Newton-Raphson IV.
  - Jaeckel (2006), "By Implication" -- robustness of vega-step Newton.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from bursahack.options.core import d1d2, norm_cdf, norm_pdf
from bursahack.options.types import Right

__all__ = ["IVResult", "price_bounds_check", "implied_vol"]


@dataclass(frozen=True)
class IVResult:
    """Outcome of an implied-vol solve.

    flag is a human-readable tag: 'ok', 'below-intrinsic', 'above-no-arb',
    'deep-OTM', 'no-converge', 'degenerate'. method in {'newton','bisection'}.
    """

    iv: float
    method: str
    iters: int
    converged: bool
    residual: float
    flag: str


def _bs_price(price_fn_S: float, K: float, T: float, r: float, q: float,
              sigma: float, right: Right) -> float:
    """European BSM price in the forward measure (self-contained for the solver).

    Kept local so the IV solver never depends on `bsm` import order; it shares
    the exact same d1/d2 primitive from `core`, so it is numerically identical
    to `bsm.price`."""
    S = price_fn_S
    if T <= 0.0 or sigma <= 0.0:
        # Deterministic intrinsic limit (discounted).
        fwd_intrinsic = S * math.exp(-q * T) - K * math.exp(-r * T)
        if right == Right.CALL:
            return max(fwd_intrinsic, 0.0)
        return max(-fwd_intrinsic, 0.0)
    dd = d1d2(S, K, T, r, q, sigma)
    disc_S = S * math.exp(-q * T)
    disc_K = K * math.exp(-r * T)
    if right == Right.CALL:
        return disc_S * dd.Nd1 - disc_K * dd.Nd2
    # Put closed form (own form, not parity-derived).
    return disc_K * norm_cdf(-dd.d2) - disc_S * norm_cdf(-dd.d1)


def _bs_vega_raw(S: float, K: float, T: float, r: float, q: float,
                 sigma: float) -> float:
    """dPrice/dSigma in RAW units (price per 1.00 of vol). Same for call & put."""
    if T <= 0.0 or sigma <= 0.0:
        return 0.0
    dd = d1d2(S, K, T, r, q, sigma)
    return S * math.exp(-q * T) * norm_pdf(dd.d1) * math.sqrt(T)


def price_bounds_check(price: float, S: float, K: float, T: float, r: float,
                       q: float, right: Right) -> dict:
    """No-arbitrage / intrinsic bounds pre-check. Run BEFORE solving.

    Returns {ok, reason, intrinsic, lower, upper}. Rejects quotes below
    intrinsic (`below-intrinsic`) and above the no-arb ceiling (`above-no-arb`).
    `intrinsic` is the discounted (forward) intrinsic, which is the true lower
    bound for a European option.
    """
    disc_S = S * math.exp(-q * T)
    disc_K = K * math.exp(-r * T)
    if right == Right.CALL:
        lower = max(disc_S - disc_K, 0.0)
        upper = disc_S
    else:
        lower = max(disc_K - disc_S, 0.0)
        upper = disc_K
    intrinsic = max((S - K) if right == Right.CALL else (K - S), 0.0)

    # Tiny tolerance so a price exactly AT a bound is accepted.
    tol = 1e-9 * max(1.0, S)
    if price < lower - tol:
        return {"ok": False, "reason": "below-intrinsic", "intrinsic": intrinsic,
                "lower": lower, "upper": upper}
    if price > upper + tol:
        return {"ok": False, "reason": "above-no-arb", "intrinsic": intrinsic,
                "lower": lower, "upper": upper}
    return {"ok": True, "reason": "ok", "intrinsic": intrinsic,
            "lower": lower, "upper": upper}


def _bisect(price: float, S: float, K: float, T: float, r: float, q: float,
            right: Right, lo: float, hi: float, tol: float,
            max_iter: int) -> tuple[float, int, bool, float]:
    """Bisection on price residual within [lo, hi]. Always converges if the
    target is bracketed (it is, after price_bounds_check passes)."""
    f_lo = _bs_price(S, K, T, r, q, lo, right) - price
    f_hi = _bs_price(S, K, T, r, q, hi, right) - price
    # If not bracketed (numerical edge), clamp to the nearer endpoint.
    if f_lo * f_hi > 0.0:
        mid = lo if abs(f_lo) < abs(f_hi) else hi
        return mid, 0, False, min(abs(f_lo), abs(f_hi))
    a, b = lo, hi
    mid = 0.5 * (a + b)
    resid = abs(f_lo)
    for i in range(1, max_iter + 1):
        mid = 0.5 * (a + b)
        f_mid = _bs_price(S, K, T, r, q, mid, right) - price
        resid = abs(f_mid)
        if resid < tol or (b - a) < tol:
            return mid, i, True, resid
        if f_lo * f_mid < 0.0:
            b = mid
            f_hi = f_mid
        else:
            a = mid
            f_lo = f_mid
    return mid, max_iter, resid < tol, resid


def implied_vol(price: float, S: float, K: float, T: float, r: float, q: float,
                right: Right, lo: float = 1e-4, hi: float = 5.0,
                tol: float = 1e-8, max_iter: int = 50) -> IVResult:
    """Solve for the BS implied vol that reprices `price`.

    Newton (vega step) seeded by Brenner-Subrahmanyam; bisection fallback when
    vega < 1e-6 at the seed (deep-OTM, forcing method=='bisection'), when a
    Newton step leaves [lo, hi], or when the residual diverges. Works in the
    forward measure so a call and its parity-paired put solve to the SAME IV.

    method in {'newton', 'bisection'}.
    """
    # --- bounds pre-check -------------------------------------------------
    bounds = price_bounds_check(price, S, K, T, r, q, right)
    if not bounds["ok"]:
        return IVResult(iv=float("nan"), method="newton", iters=0,
                        converged=False, residual=float("nan"),
                        flag=bounds["reason"])
    if T <= 0.0:
        return IVResult(iv=float("nan"), method="newton", iters=0,
                        converged=False, residual=float("nan"),
                        flag="degenerate")
    # A price sitting on a bound has a degenerate (0 or inf) IV; serve the
    # nearest bracket endpoint deterministically.
    if price <= bounds["lower"] + tol:
        return IVResult(iv=lo, method="bisection", iters=0, converged=True,
                        residual=abs(_bs_price(S, K, T, r, q, lo, right) - price),
                        flag="deep-OTM")

    # --- Brenner-Subrahmanyam ATM seed -----------------------------------
    seed = math.sqrt(2.0 * math.pi / T) * price / S if S > 0.0 else 0.2
    sigma = min(max(seed, lo), hi)

    # deep-OTM trigger: vega at the BS seed < 1e-6 -> bisection (deterministic).
    seed_vega = _bs_vega_raw(S, K, T, r, q, sigma)
    if seed_vega < 1e-6:
        iv, iters, conv, resid = _bisect(price, S, K, T, r, q, right, lo, hi,
                                         tol, max_iter)
        return IVResult(iv=iv, method="bisection", iters=iters, converged=conv,
                        residual=resid, flag="deep-OTM" if conv else "no-converge")

    # --- Newton with vega step -------------------------------------------
    prev_resid = float("inf")
    for i in range(1, max_iter + 1):
        model = _bs_price(S, K, T, r, q, sigma, right)
        diff = model - price
        resid = abs(diff)
        if resid < tol:
            return IVResult(iv=sigma, method="newton", iters=i, converged=True,
                            residual=resid, flag="ok")
        v = _bs_vega_raw(S, K, T, r, q, sigma)
        # Fallback conditions: vega collapse OR diverging residual.
        if v < 1e-6 or resid > 2.0 * prev_resid:
            iv, it_b, conv, resid_b = _bisect(price, S, K, T, r, q, right,
                                              lo, hi, tol, max_iter)
            return IVResult(iv=iv, method="bisection", iters=i + it_b,
                            converged=conv, residual=resid_b,
                            flag="ok" if conv else "no-converge")
        step = diff / v
        new_sigma = sigma - step
        # Step left the bracket -> bisection fallback.
        if new_sigma <= lo or new_sigma >= hi:
            iv, it_b, conv, resid_b = _bisect(price, S, K, T, r, q, right,
                                              lo, hi, tol, max_iter)
            return IVResult(iv=iv, method="bisection", iters=i + it_b,
                            converged=conv, residual=resid_b,
                            flag="ok" if conv else "no-converge")
        prev_resid = resid
        sigma = new_sigma

    # Newton exhausted iterations without converging -> final bisection sweep.
    iv, it_b, conv, resid_b = _bisect(price, S, K, T, r, q, right, lo, hi,
                                      tol, max_iter)
    return IVResult(iv=iv, method="bisection", iters=max_iter + it_b,
                    converged=conv, residual=resid_b,
                    flag="ok" if conv else "no-converge")
