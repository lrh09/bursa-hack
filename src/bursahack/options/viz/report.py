"""HTML tearsheet surface for the options viz layer (``viz.report``).

The CLI (``scripts/run_options_analysis.py``) imports
``bursahack.options.viz.report`` and calls ``build_html_report(ctx, out_path,
mode="book")``; the math-camp report module also exposes the higher-level
``build_report(book, account, out_path)`` orchestrator. This module is a thin
re-export of ``bursahack.options.report`` -- it adds NO templating or assembly
logic, so the self-contained-tearsheet contract is enforced in exactly one
place.

Import direction: ``viz.report`` -> ``report`` -> (lazily) ``viz.charts`` ->
``charts``. ``report`` only imports ``viz.charts`` *inside* ``_render_charts``,
so the load-time chain is acyclic.
"""
from __future__ import annotations

from bursahack.options.report import *  # noqa: F401,F403  (re-export public surface)

# Pin the entry points the CLI / notebooks call by name.
from bursahack.options.report import (  # noqa: F401
    ReportContext,
    build_ctx,
    build_html_report,
    build_pdf_report,
    build_report,
    fig_to_base64,
    png_path_to_base64,
)

__all__ = [
    "ReportContext",
    "build_html_report",
    "build_report",
    "build_ctx",
    "build_pdf_report",
    "fig_to_base64",
    "png_path_to_base64",
]
