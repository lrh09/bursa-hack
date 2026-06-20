"""Options analysis & stress-testing toolkit.

A general engine for pricing, modelling and stress-testing equity-option
positions on ANY US underlying across arbitrary strikes and expiries.

Born from a live TSLA options-book review (2026-06): Black-Scholes pricing +
greeks, multi-leg structure modelling (verticals, condors, collars, PMCC),
net-book delta/exposure aggregation, scenario P&L stress grids, defined-risk &
margin checks, roll analysis, income-posture advice, and self-contained
HTML/PDF reports with payoff diagrams and scenario heatmaps.

Public API
----------
This module re-exports the headline symbols of the subpackage so callers can
``from bursahack.options import Leg, MarketState, price, ...`` without knowing
the internal module layout.

Each re-export group is wrapped in ``try/except`` so that a partially-importable
subpackage (e.g. an optional dependency missing for ``report``, or a
consumer-camp glue module such as ``structure`` / ``bookio`` not yet present on
this checkout) never breaks ``import bursahack.options``. ``__all__`` only
advertises names that actually resolved.
"""
from __future__ import annotations

__all__: list[str] = []


def _export(*names: str) -> None:
    """Record successfully-bound public names in ``__all__`` (dedup, ordered)."""
    g = globals()
    for name in names:
        if name in g and name not in __all__:
            __all__.append(name)


# --- Core value objects (instruments) ---------------------------------------
# These are the canonical Right/Style/Measure/Leg/MarketState/Position/Book/
# Account types. bs.py defines its own enum-duals with identical values, but
# instruments.py holds the canonical objects the rest of the toolkit composes.
try:
    from .instruments import (
        Right,
        Style,
        Measure,
        Leg,
        MarketState,
        Position,
        Book,
        AccountProfile,
        Account,
    )
    _export(
        "Right",
        "Style",
        "Measure",
        "Leg",
        "MarketState",
        "Position",
        "Book",
        "AccountProfile",
        "Account",
    )
except Exception:  # pragma: no cover - defensive import guard
    pass

# --- Black-Scholes pricing & greeks -----------------------------------------
try:
    from .bs import price, all_greeks
    _export("price", "all_greeks")
except Exception:  # pragma: no cover - defensive import guard
    pass

# --- Implied volatility ------------------------------------------------------
try:
    from .iv import implied_vol
    _export("implied_vol")
except Exception:  # pragma: no cover - defensive import guard
    pass

# --- Structure builders ------------------------------------------------------
# Consumer-camp wrapper module; may not be present / fully wired on every
# checkout. Re-export the headline multi-leg builders when available.
try:
    from .structure import (
        vertical,
        straddle,
        strangle,
        iron_condor,
        iron_butterfly,
        butterfly,
        calendar,
        diagonal,
        collar,
        covered_call,
        cash_secured_put,
        pmcc,
    )
    _export(
        "vertical",
        "straddle",
        "strangle",
        "iron_condor",
        "iron_butterfly",
        "butterfly",
        "calendar",
        "diagonal",
        "collar",
        "covered_call",
        "cash_secured_put",
        "pmcc",
    )
except Exception:  # pragma: no cover - defensive import guard
    pass

# --- Book I/O ----------------------------------------------------------------
# Consumer-camp glue module; guarded so a missing/partial bookio never breaks
# the package import.
try:
    from .bookio import load_book
    _export("load_book")
except Exception:  # pragma: no cover - defensive import guard
    pass

# --- Reporting ---------------------------------------------------------------
# report.py pulls in the viz subpackage (theme + charts) and jinja2/reportlab;
# guard so an unavailable optional dependency degrades gracefully.
try:
    from .report import build_report
    _export("build_report")
except Exception:  # pragma: no cover - defensive import guard
    pass
