"""Pricing-core re-export shim (consumer-camp contract name).

Several consumer-side modules (``iv``, ``prob``, ``strategy``) and tests import
the low-level Black-Scholes-Merton primitives under the name
``bursahack.options.core``. The canonical implementations live in
:mod:`bursahack.options.bs` (design law L1: ONE pricing core). This module is a
thin, side-effect-free re-export so both naming camps resolve to the *same*
objects — no second implementation, no drift.
"""
from __future__ import annotations

from bursahack.options.bs import (
    D1D2,
    d1d2,
    implied_forward,
    norm_cdf,
    norm_pdf,
)

__all__ = [
    "d1d2",
    "norm_cdf",
    "norm_pdf",
    "D1D2",
    "implied_forward",
]
