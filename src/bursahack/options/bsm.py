"""Black-Scholes-Merton pricing/greeks re-export shim (consumer-camp name).

Consumer-side modules (``strategy``) and tests import the full European
pricing + greek surface under the name ``bursahack.options.bsm``. The canonical
implementations live in :mod:`bursahack.options.bs` (design law L1: ONE pricing
core). This is a thin re-export so ``bsm`` and ``bs`` are literally the same
functions/classes — passing values across the two camps already resolves
because they share identity here.
"""
from __future__ import annotations

from bursahack.options.bs import (
    D1D2,
    Greeks,
    Right,
    all_greeks,
    d1d2,
    first_order,
    implied_forward,
    norm_cdf,
    norm_pdf,
    parity_residual,
    price,
    spot_time_greeks,
    vol_greeks,
)

__all__ = [
    "price",
    "Greeks",
    "Right",
    "D1D2",
    "first_order",
    "vol_greeks",
    "spot_time_greeks",
    "all_greeks",
    "parity_residual",
    "implied_forward",
    "d1d2",
    "norm_cdf",
    "norm_pdf",
]
