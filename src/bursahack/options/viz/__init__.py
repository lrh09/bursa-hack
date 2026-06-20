"""Visualization layer for the options toolkit (charts + theme + HTML report).

This subpackage is the *consumer-facing* viz surface the CLI and report
orchestrator import as ``bursahack.options.viz.{charts,theme,report}``. Each
module is a thin re-export of the tested math-camp implementation that lives one
level up:

  * ``viz.theme``   -> ``charts.apply_theme`` / ``save`` / ``to_base64`` + PALETTE
  * ``viz.charts``  -> every chart fn in ``bursahack.options.charts``
  * ``viz.report``  -> ``bursahack.options.report`` (build_report / build_html_report)

No analytics or math live here -- only the package layout the contract specifies.
"""
from __future__ import annotations

__all__ = ["charts", "theme", "report"]
