"""Shared chart theme for the options viz layer (``viz.theme``).

The math-camp ``bursahack.options.charts`` module is the single source of the
dark desk palette + the ``apply_theme`` rcParams (mirrored from
``web/public/crypto_deflated_report.html``). This module exposes that style under
the ``viz.theme`` name the contract puts next to ``viz.charts`` so the whole
report shares one style source.

IMPORTANT -- recursion guard
----------------------------
``charts.save`` / ``charts.to_base64`` deliberately *delegate* to
``viz.theme.save`` / ``viz.theme.to_base64`` **when those attributes exist**, and
fall back to their own bodies otherwise (charts owns only charts.py and is
written to be self-contained). If this module re-exported ``charts.save`` /
``charts.to_base64`` straight back, ``charts.save -> theme.save -> charts.save``
would recurse forever. So ``save`` / ``to_base64`` here are **terminal**
implementations (their own bodies, never routing back through charts).

``apply_theme`` is terminal inside ``charts`` (it never calls ``viz.theme``), so
re-exporting it is safe and keeps a single rcParams source.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING

from bursahack.options.charts import (
    APPENDIX_DPI,
    INLINE_DPI,
    OPTIONS_RESULTS,
    _PALETTE as PALETTE,
    apply_theme,  # terminal in charts -> safe to re-export
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from matplotlib.figure import Figure

__all__ = [
    "PALETTE",
    "apply_theme",
    "save",
    "to_base64",
    "INLINE_DPI",
    "APPENDIX_DPI",
    "OPTIONS_RESULTS",
]


def _close(fig: "Figure") -> None:
    """Close a figure to free memory (hard rule: plt.close after every render)."""
    import matplotlib.pyplot as plt

    plt.close(fig)


def save(fig: "Figure", path: str | Path, dpi: int = INLINE_DPI) -> str:
    """Write ``fig`` to PNG under results/options/ (or an absolute path), close it,
    return the written path. Terminal implementation (does NOT call charts.save).
    """
    p = Path(path)
    if not p.is_absolute():
        p = OPTIONS_RESULTS / p
    if p.suffix.lower() != ".png":
        p = p.with_suffix(".png")
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    _close(fig)
    return str(p)


def to_base64(fig: "Figure", dpi: int = INLINE_DPI) -> str:
    """Encode ``fig`` as a ``data:image/png;base64,...`` URI and close it. Terminal
    implementation (does NOT call charts.to_base64)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    _close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
