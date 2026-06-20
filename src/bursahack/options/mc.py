"""Monte-Carlo re-export shim (consumer-camp contract name).

The terminal-simulation + P&L-distribution engine is implemented once in
:mod:`bursahack.options.montecarlo`. Consumer-side modules and the CLI import it
under the name ``bursahack.options.mc``. This is a thin re-export so both names
resolve to the same functions — no second sampler, no drift.

Note: ``terminal_payoff_array`` is intentionally NOT re-exported here — the
canonical module exposes ``_pnl_array`` (private) rather than a public
``terminal_payoff_array``, so there is nothing to forward. The public,
contract-level surface is the five functions below.
"""
from __future__ import annotations

from bursahack.options.montecarlo import (
    pnl_distribution,
    pnl_percentiles,
    reconcile,
    simulate_terminal,
    var_cvar,
)

__all__ = [
    "simulate_terminal",
    "pnl_distribution",
    "var_cvar",
    "pnl_percentiles",
    "reconcile",
]
