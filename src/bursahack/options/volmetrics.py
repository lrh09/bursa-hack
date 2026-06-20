"""Volatility metrics: expected move, IV rank / percentile, variance-risk premium.

Small, pure, self-contained desk helpers that sit ALONGSIDE the heavier
estimators in :mod:`bursahack.options.vol` (term structure, skew, the historical
realized-vol family). Nothing here touches the wire or the pricing core — every
function is a closed-form transform of a handful of floats / a short history, so
the module imports on a bare interpreter and is trivially testable.

Sign / unit conventions (per :mod:`bursahack.options.types`):
  - vols are DECIMALS (0.48 == 48%), never percent.
  - time ``T`` is in YEARS.
  - an "expected move" is returned in the SAME units as the spot it is computed
    from (i.e. price points), as a non-negative magnitude.

Definitions
-----------
One-sigma expected move (lognormal, drift-free first order)::

    EM_1σ = S * sigma * sqrt(T)

GOLDEN: ``expected_move_sigma(396.4, 0.48, 0.25) == 95.136``.

Straddle-implied expected move — the market's own one-sigma read, the ATM
straddle mid price (call + put) is ≈ the +/- one-sigma move::

    EM_straddle ≈ straddle_price          (absolute price move)

IV rank vs IV percentile (both 0..100 over a history window):
  - ``iv_rank``       = (iv - min) / (max - min) * 100   — position in the RANGE.
  - ``iv_percentile`` = fraction of history at/below iv * 100 — position in the
    DISTRIBUTION (robust to a single outlier high/low that distorts the range).

Variance-risk premium — implied variance richer than realized variance::

    VRP_var = iv^2 - hv^2          (variance space; the canonical form)
    VRP_vol = iv  - hv             (vol-point space; convenience)

A positive VRP means options are pricing more variance than has been realized
(the classic short-premium edge); negative means realized is outrunning implied.

References:
  - Natenberg, "Option Volatility & Pricing" (expected move, ATM straddle ≈ 1σ).
  - tastytrade / Sinclair (IV rank vs IV percentile, variance-risk premium).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "expected_move_sigma",
    "expected_move_straddle",
    "iv_rank",
    "iv_percentile",
    "variance_risk_premium",
]


# ---------------------------------------------------------------------------
# Expected move
# ---------------------------------------------------------------------------
def expected_move_sigma(S: float, sigma: float, T: float) -> float:
    """One-sigma expected move ``S * sigma * sqrt(T)`` (price points, magnitude).

    The drift-free first-order lognormal one-standard-deviation move. ``sigma`` is
    a decimal vol, ``T`` is in years. Returns a non-negative magnitude.

    GOLDEN: ``expected_move_sigma(396.4, 0.48, 0.25) == 95.136``.
    """
    if T < 0.0:
        raise ValueError("expected_move_sigma requires T >= 0")
    if sigma < 0.0:
        raise ValueError("expected_move_sigma requires sigma >= 0")
    return abs(S) * sigma * math.sqrt(T)


def expected_move_straddle(straddle_price: float) -> float:
    """Market-implied one-sigma move from the ATM straddle mid (absolute move).

    The ATM straddle mid (call + put premium at the ATM strike) is the market's
    own ≈ one-sigma expected move over the straddle's tenor. We return it as a
    non-negative absolute price move; the caller already supplies a per-share
    straddle price (not dollar-multiplied).
    """
    return abs(straddle_price)


# ---------------------------------------------------------------------------
# IV rank / percentile over a history window
# ---------------------------------------------------------------------------
def _clean_history(iv_history: Sequence[float]) -> list[float]:
    """Drop non-finite entries from a history series; raise if nothing remains."""
    hist = [float(x) for x in iv_history
            if x is not None and not (isinstance(x, float) and math.isnan(x))]
    hist = [x for x in hist if math.isfinite(x)]
    if not hist:
        raise ValueError("iv_history is empty (no finite samples)")
    return hist


def iv_rank(current_iv: float, iv_history: Sequence[float]) -> float:
    """IV rank in ``[0, 100]``: position of ``current_iv`` within the history RANGE.

    ``(iv - min) / (max - min) * 100``, clamped to ``[0, 100]``. A flat history
    (max == min) returns ``0.0`` (no range to rank against). ``current_iv`` may
    sit outside the historical range (e.g. a fresh all-time high) — it clamps to
    the 0 / 100 endpoints rather than overshooting.
    """
    hist = _clean_history(iv_history)
    lo, hi = min(hist), max(hist)
    if hi <= lo:
        return 0.0
    rank = (float(current_iv) - lo) / (hi - lo) * 100.0
    return _clip01_100(rank)


def iv_percentile(current_iv: float, iv_history: Sequence[float]) -> float:
    """IV percentile in ``[0, 100]``: fraction of history at/below ``current_iv``.

    ``count(h <= current_iv) / n * 100``. Unlike :func:`iv_rank` this reads the
    DISTRIBUTION, so a single outlier high/low does not distort it. Returns a
    value in ``[0, 100]``.
    """
    hist = _clean_history(iv_history)
    iv = float(current_iv)
    at_or_below = sum(1 for h in hist if h <= iv)
    return _clip01_100(at_or_below / len(hist) * 100.0)


def _clip01_100(x: float) -> float:
    return 0.0 if x < 0.0 else 100.0 if x > 100.0 else x


# ---------------------------------------------------------------------------
# Variance-risk premium
# ---------------------------------------------------------------------------
def variance_risk_premium(iv: float, hv: float, *, space: str = "variance") -> float:
    """Variance-risk premium: implied richness over realized.

    ``space='variance'`` (default) -> ``iv*iv - hv*hv`` (the canonical
    variance-space VRP). ``space='vol'`` -> ``iv - hv`` (vol-point convenience).
    Positive => options price more variance than has been realized (short-premium
    edge); negative => realized is outrunning implied.
    """
    if space == "variance":
        return iv * iv - hv * hv
    if space == "vol":
        return iv - hv
    raise ValueError(f"space must be 'variance' or 'vol', got {space!r}")
