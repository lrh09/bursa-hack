"""Charts surface for the options viz layer (``viz.charts``).

The CLI (``scripts/run_options_analysis.py``) and the report orchestrator
(``bursahack.options.report``) import ``bursahack.options.viz.charts`` and call
``render_all_charts`` + the individual chart functions on it. This module is a
thin re-export of the tested math-camp implementation in
``bursahack.options.charts`` -- it adds NO new chart logic, so there is exactly
one source of payoff/greek truth (design law L3).

``from bursahack.options.charts import *`` pulls everything ``charts.__all__``
exposes; the explicit imports below pin the names the consumers reference so a
static check (and re-export) is unambiguous.
"""
from __future__ import annotations

from bursahack.options.charts import *  # noqa: F401,F403  (re-export the public surface)

# Pin the names consumers call by name so the re-export is explicit + checkable.
from bursahack.options.charts import (  # noqa: F401
    APPENDIX_DPI,
    INLINE_DPI,
    OPTIONS_RESULTS,
    _PALETTE,
    apply_theme,
    dashboard,
    delta_ladder,
    expiration_timeline,
    greeks_vs_spot,
    mc_histogram,
    payoff_diagram,
    pnl_surface_spot_time,
    pnl_surface_spot_vol,
    probability_cone,
    render_all_charts,
    save,
    scenario_tornado,
    to_base64,
    vol_cone,
    vol_smile,
    vol_term_structure,
)

# Re-export the palette under the friendlier name too (mirrors viz.theme.PALETTE).
PALETTE = _PALETTE

__all__ = [
    # plumbing
    "apply_theme",
    "save",
    "to_base64",
    "PALETTE",
    "OPTIONS_RESULTS",
    "INLINE_DPI",
    "APPENDIX_DPI",
    # charts
    "payoff_diagram",
    "greeks_vs_spot",
    "pnl_surface_spot_vol",
    "pnl_surface_spot_time",
    "vol_smile",
    "vol_term_structure",
    "vol_cone",
    "probability_cone",
    "mc_histogram",
    "delta_ladder",
    "scenario_tornado",
    "expiration_timeline",
    # composition
    "dashboard",
    "render_all_charts",
]
