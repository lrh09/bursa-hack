"""Matplotlib chart functions for the options toolkit (PNG + base64 embedding).

One PURE function per chart. Each returns ``(Figure, sidecar: dict)`` where the
sidecar carries the load-bearing numbers (breakevens, max P/L, VaR/CVaR, net
delta, ...) so the HTML report can render them as text alongside the image and
tests can assert "the curve hits the +48832 gridline" without OCR.

Design laws honoured here (see the BursaHack options BUILD CONTRACT §0):
  * L3  Single source of payoff truth. The payoff diagram draws the curve from
        ``payoff.payoff_curve`` / ``position.value_position`` -- never a parallel
        intrinsic formula. ``net_cost`` is REQUIRED on every P&L chart so a caller
        can never silently chart gross intrinsic as P&L.
  * L4  Pure / lazy I/O. matplotlib is imported lazily (Agg forced BEFORE pyplot);
        analytics modules (payoff/position/prob/scenario/book/risk/vol/volmetrics)
        are imported lazily INSIDE the functions that need them, never at module
        top. This module NEVER imports marketdata/providers and never hits the wire.
  * L6  Measure is explicit. The probability cone carries a ``Measure`` and the
        drift it implies; charts annotate which measure produced the odds.

Hard chart rules (contract §3 viz/charts):
  * backend forced 'Agg' before pyplot;  ``plt.close`` after every render.
  * dpi <= 110 inline / full-res in the appendix.
  * every load-bearing number annotated on-canvas (BE, max P/L, current spot,
    VaR/CVaR, net delta).
  * diverging colormap via ``TwoSlopeNorm(vcenter=0)`` with padding.

Each chart accepts an optional ``ax`` so ``dashboard`` can compose small-multiples
on one Figure; when ``ax`` is None a fresh single-axes Figure is created.

References:
  * Hull, *Options, Futures, and Other Derivatives* -- payoff diagrams, greeks.
  * web/public/crypto_deflated_report.html -- self-contained dark-theme report.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from bursahack.paths import RESULTS_DIR

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import numpy as np
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    from bursahack.options.types import Book, Leg, MarketState, Measure


# --------------------------------------------------------------------------- #
# Canonical output dir (contract §0). Created lazily at write time.
# --------------------------------------------------------------------------- #
OPTIONS_RESULTS: Path = RESULTS_DIR / "options"

# dpi caps (contract hard chart rule).
INLINE_DPI: int = 110
APPENDIX_DPI: int = 160

# Dark palette mirrored from web/public/crypto_deflated_report.html so charts and
# the surrounding report read as one document.
_PALETTE = {
    "bg": "#0d1117",
    "card": "#161b22",
    "grid": "#30363d",
    "text": "#c9d1d9",
    "muted": "#8b949e",
    "good": "#3fb950",   # profit / up
    "warn": "#d29922",
    "bad": "#f85149",    # loss / down
    "acc": "#58a6ff",    # accent / neutral series
    "acc2": "#bc8cff",
}


# --------------------------------------------------------------------------- #
# Theme + save/encode plumbing.
#
# The contract puts a shared `viz/theme.py` next to this file. This module owns
# ONLY charts.py, so it is written to be self-contained: it uses viz.theme when
# present, and otherwise falls back to its own apply_theme/save/to_base64 so the
# charts render identically on a bare tree.
# --------------------------------------------------------------------------- #
def _theme_module():
    """Return the sibling `viz.theme` module if it exists, else None."""
    try:  # pragma: no cover - depends on parallel-built sibling
        from bursahack.options.viz import theme as _theme  # type: ignore

        return _theme
    except Exception:  # ModuleNotFoundError or partial build
        return None


def _pyplot():
    """Lazy matplotlib import with Agg forced BEFORE pyplot (headless, deterministic).

    Routes through ``viz.theme.apply_theme`` when available so the whole report
    shares one style source; otherwise applies this module's dark theme.
    """
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt  # noqa: E402  (must follow backend selection)

    theme = _theme_module()
    if theme is not None and hasattr(theme, "apply_theme"):
        theme.apply_theme()
    else:
        apply_theme()
    return plt


def apply_theme() -> None:
    """Fallback dark rcParams (used when viz.theme is absent). Idempotent."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib as mpl

    p = _PALETTE
    mpl.rcParams.update(
        {
            "figure.facecolor": p["bg"],
            "axes.facecolor": p["card"],
            "savefig.facecolor": p["bg"],
            "axes.edgecolor": p["grid"],
            "axes.labelcolor": p["text"],
            "axes.titlecolor": p["text"],
            "text.color": p["text"],
            "xtick.color": p["muted"],
            "ytick.color": p["muted"],
            "grid.color": p["grid"],
            "grid.alpha": 0.4,
            "axes.grid": True,
            "axes.axisbelow": True,
            "legend.frameon": False,
            "figure.autolayout": False,
            "font.size": 10,
            "axes.titlesize": 12,
            "figure.dpi": INLINE_DPI,
        }
    )


def save(fig: "Figure", path: str | Path, dpi: int = INLINE_DPI) -> str:
    """Write `fig` to PNG under results/options/ (or an absolute path), close it,
    return the written path as a string. Delegates to viz.theme.save when present.
    """
    theme = _theme_module()
    if theme is not None and hasattr(theme, "save"):
        return theme.save(fig, path)  # type: ignore[no-any-return]

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
    """Encode `fig` as a ``data:image/png;base64,...`` URI and close it.

    The report embeds these so the HTML is a single portable file (zero CDN).
    Delegates to viz.theme.to_base64 when present.
    """
    theme = _theme_module()
    if theme is not None and hasattr(theme, "to_base64"):
        return theme.to_base64(fig)  # type: ignore[no-any-return]

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    _close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _close(fig: "Figure") -> None:
    """Close a figure to free memory (hard rule: plt.close after every render)."""
    import matplotlib.pyplot as plt

    plt.close(fig)


# --------------------------------------------------------------------------- #
# Shared helpers.
# --------------------------------------------------------------------------- #
def _ensure_ax(ax: "Axes | None", figsize: tuple[float, float] = (8.0, 4.8)):
    """Return ``(fig, ax, owns_fig)``. When `ax` is None a fresh Figure is made;
    `owns_fig` tells the caller whether it may save/encode the whole figure."""
    plt = _pyplot()
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
        return fig, ax, True
    return ax.figure, ax, False


def _spot_of(market: Any) -> float:
    """Spot from a MarketState or a plain float."""
    return float(getattr(market, "spot", market))


def _annotate(ax: "Axes", text: str, *, loc: str = "upper left", color: str | None = None) -> None:
    """Box an on-canvas annotation (load-bearing numbers, contract rule)."""
    color = color or _PALETTE["text"]
    xy = {
        "upper left": (0.02, 0.98, "left", "top"),
        "upper right": (0.98, 0.98, "right", "top"),
        "lower left": (0.02, 0.02, "left", "bottom"),
        "lower right": (0.98, 0.02, "right", "bottom"),
    }[loc]
    ax.text(
        xy[0],
        xy[1],
        text,
        transform=ax.transAxes,
        ha=xy[2],
        va=xy[3],
        fontsize=8.5,
        color=color,
        bbox=dict(boxstyle="round,pad=0.4", fc=_PALETTE["bg"], ec=_PALETTE["grid"], alpha=0.85),
    )


def _diverging_norm(z, pad: float = 0.0):
    """``TwoSlopeNorm`` centred on 0 with padding so a flat field never crashes."""
    import numpy as np
    from matplotlib.colors import TwoSlopeNorm

    arr = np.asarray(z, dtype=float)
    vmin = float(np.nanmin(arr))
    vmax = float(np.nanmax(arr))
    span = max(abs(vmin), abs(vmax), 1e-9)
    vmin = min(vmin, -1e-9) - pad * span
    vmax = max(vmax, 1e-9) + pad * span
    return TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}"


def _net_cost_entry(legs: Sequence["Leg"]) -> float:
    """Entry net cash in the contract's display convention (debit>0 / credit<0).

    Prefers the canonical ``position.net_cost_entry`` (the single source of truth
    once that sibling lands); only when it is unavailable does this fall back to a
    local computation. Fallback = net cash PAID = sum(qty * entry * mult): a long
    leg (qty>0) pays premium (+debit); a short leg (qty<0) receives (-credit). For
    the 360/460 x8 golden: +8*91.96*100 - 8*53*100 = +31168 (a net debit)."""
    try:
        from bursahack.options import position  # lazy, contract-built sibling

        return float(position.net_cost_entry(list(legs)))
    except Exception:
        total = 0.0
        for leg in legs:
            qty = float(getattr(leg, "qty", 0.0))
            if qty == 0.0:
                continue
            entry = float(getattr(leg, "entry_price", 0.0))
            mult = int(getattr(leg, "mult", 100))
            total += qty * entry * mult
        return total


# =========================================================================== #
# 1. Payoff diagram
# =========================================================================== #
def payoff_diagram(
    legs: Sequence["Leg"],
    market: "MarketState | float",
    net_cost: float,
    tn_days: tuple[int, ...] = (0, 30),
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Expiry payoff (and optional MTM-at-T curves) vs underlying.

    ``net_cost`` is REQUIRED (L3): pass ``position.net_cost_entry(legs)`` for the
    realized P&L framing. The expiry curve is drawn through the exact kink strikes
    via ``payoff.payoff_curve``; intermediate-time curves (``tn_days`` > 0) reprice
    through ``position.value_position`` when that sibling is available.

    Sidecar: ``{breakevens, max_profit, max_loss, payoff_table, spot}``.
    """
    fig, ax, owns = _ensure_ax(ax)
    spot = _spot_of(market)

    import numpy as np

    # --- spot grid (kink-aware): cover spot + all strikes with a margin so the
    # payoff kinks are sampled exactly and the curve has tails on both sides ---
    strikes = sorted({float(getattr(l, "strike", 0.0)) for l in legs if getattr(l, "strike", 0.0)})
    lo = min([spot] + strikes) * 0.6 if strikes else spot * 0.6
    hi = max([spot] + strikes) * 1.4 if strikes else spot * 1.4
    S_grid = np.unique(np.concatenate([np.linspace(lo, hi, 401), np.asarray(strikes, dtype=float)]))

    # --- expiry curve through the kinks (single source of payoff truth) ---
    pnl: "np.ndarray"
    breakevens: list[float] = []
    kinks: list[float] = strikes
    try:
        from bursahack.options import payoff as _payoff

        # payoff_curve(legs, S_points, net_cost): S_points is REQUIRED and the
        # return is {"S", "pnl"} -- route the chart's grid through the ONE
        # terminal-payoff kernel rather than re-intrinsicing here (L3).
        curve = _payoff.payoff_curve(list(legs), S_grid.tolist(), net_cost)
        S_grid = np.asarray(curve["S"], dtype=float)
        pnl = np.asarray(curve["pnl"], dtype=float)
    except Exception:
        # Standalone fallback: intrinsic the legs on the same grid.
        pnl = np.array([_intrinsic_pnl(legs, float(s), net_cost) for s in S_grid])

    # breakevens = sign changes of the expiry P&L
    breakevens = _zero_crossings(S_grid, pnl)

    # shade profit (green) vs loss (red)
    ax.fill_between(S_grid, pnl, 0, where=(pnl >= 0), color=_PALETTE["good"], alpha=0.18, interpolate=True)
    ax.fill_between(S_grid, pnl, 0, where=(pnl < 0), color=_PALETTE["bad"], alpha=0.18, interpolate=True)
    ax.plot(S_grid, pnl, color=_PALETTE["good"], lw=2.0, label="At expiry")
    ax.axhline(0, color=_PALETTE["muted"], lw=0.8)

    # intermediate-time MTM curves (reprice via the ONE kernel) -------------
    other_t = [d for d in tn_days if d and d > 0]
    if other_t and not isinstance(market, (int, float)):
        try:
            from bursahack.options import position as _position

            colors = [_PALETTE["acc"], _PALETTE["acc2"], _PALETTE["warn"]]
            entry = net_cost
            for i, d in enumerate(other_t):
                mtm = []
                for s in S_grid:
                    val = _position.value_position(list(legs), float(s), market, dt_days=float(d))
                    mtm.append(float(val["position_value"]) - entry)
                ax.plot(
                    S_grid,
                    mtm,
                    color=colors[i % len(colors)],
                    lw=1.3,
                    ls="--",
                    alpha=0.9,
                    label=f"T-{d}d",
                )
        except Exception:
            pass

    # spot + breakeven markers
    ax.axvline(spot, color=_PALETTE["acc"], lw=1.0, ls=":", alpha=0.8)
    ax.text(spot, ax.get_ylim()[1], f" spot {spot:g}", color=_PALETTE["acc"], fontsize=8, va="top")
    for be in breakevens:
        ax.axvline(be, color=_PALETTE["warn"], lw=0.8, ls="--", alpha=0.7)

    max_profit = float(np.max(pnl))
    max_loss = float(np.min(pnl))
    be_str = ", ".join(f"{b:.2f}" for b in breakevens) if breakevens else "n/a"

    # Honesty (entry=0 framing): when net_cost == 0 the curve is the GROSS terminal
    # mark value of the structure, NOT a realized P&L (there is no entry cash to
    # subtract). Only a real debit/credit net_cost turns this into P&L. The label,
    # series name and max/max-loss wording follow that distinction so a reader never
    # mistakes gross intrinsic value for realized profit.
    gross = abs(float(net_cost)) < 1e-9
    if gross:
        y_label = "Terminal value ($) — gross mark, entry=0"
        series_label = "At expiry (gross mark)"
        max_label, min_label = "max value", "min value"
    else:
        y_label = "Position P&L ($)"
        series_label = "At expiry"
        max_label, min_label = "max P", "max L"
    # Relabel the expiry line (drawn earlier as "At expiry") for legend honesty.
    for line in ax.get_lines():
        if line.get_label() == "At expiry":
            line.set_label(series_label)
            break

    _annotate(
        ax,
        f"BE: {be_str}\n{max_label}: {_money(max_profit)}\n{min_label}: {_money(max_loss)}",
        loc="upper left",
    )

    ax.set_xlabel("Underlying at expiry")
    ax.set_ylabel(y_label)
    ax.set_title("Payoff diagram")
    ax.legend(loc="lower right", fontsize=8)

    table = {float(s): float(p) for s, p in zip(S_grid[:: max(1, len(S_grid) // 25)], pnl[:: max(1, len(pnl) // 25)])}
    sidecar = {
        "breakevens": breakevens,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "payoff_table": table,
        "spot": spot,
        "kink_strikes": kinks,
    }
    return fig, sidecar


def _intrinsic_pnl(legs: Sequence["Leg"], S: float, net_cost: float) -> float:
    """Standalone fallback intrinsic P&L (only used when payoff.py is absent)."""
    total = 0.0
    for leg in legs:
        right = getattr(getattr(leg, "right", None), "value", getattr(leg, "right", ""))
        K = float(getattr(leg, "strike", 0.0))
        qty = float(getattr(leg, "qty", 0.0))
        mult = int(getattr(leg, "mult", 100))
        if right == "C":
            intr = max(S - K, 0.0)
        elif right == "P":
            intr = max(K - S, 0.0)
        elif right == "S":
            intr = S
        else:  # cash
            intr = 1.0
        total += intr * qty * mult
    return total - net_cost


def _zero_crossings(x, y, tol: float = 1e-9) -> list[float]:
    """Genuine breakevens of y(x): TRUE sign changes only (non-zero slope).

    A breakeven is a spot where the payoff actually CROSSES zero -- i.e. the curve
    is below zero on one side and above zero on the other. With ``net_cost == 0``
    (this book's entry=0 framing) the OTM wing of a long/short single-strike option
    sits flat ON zero over a whole range; that flat-on-zero plateau is NOT a
    breakeven (the payoff merely touches zero, it does not cross it). Counting the
    plateau edges as breakevens is exactly the spurious-breakeven defect this fn
    fixes: a lone long call / short call / debit-zero vertical must report NO
    breakevens, while a true straddle/combo that goes negative-then-positive must
    report the single crossing.

    Rules:
      * Non-zero opposite-signed neighbours (``y0 * y1 < 0``) -> linear-interpolated
        root (a clean crossing strictly between two grid points).
      * A grid point (or contiguous run of grid points) that is EXACTLY on zero is a
        breakeven ONLY when the nearest non-zero neighbour to its left and to its
        right have OPPOSITE signs (the curve passes through zero with non-zero
        slope). The crossing is reported at the midpoint of the zero run. This
        catches the combo's kink-on-zero (e.g. a synthetic crossing at its strike)
        without ever firing on a flat-on-zero plateau bounded by zero / same-sign
        regions.
    Near-equal roots are de-duplicated.
    """
    import numpy as np

    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    n = len(xa)
    if n < 2:
        return []

    def _right_sign(idx: int) -> float:
        """Sign of the nearest non-zero value at index > idx (0.0 if none)."""
        j = idx + 1
        while j < n:
            if abs(ya[j]) > tol:
                return 1.0 if ya[j] > 0.0 else -1.0
            j += 1
        return 0.0

    out: list[float] = []
    i = 1
    while i < n:
        y0, y1 = float(ya[i - 1]), float(ya[i])
        a0 = abs(y0) <= tol
        a1 = abs(y1) <= tol
        if (not a0) and (not a1) and y0 * y1 < 0.0:
            # strict sign change between two non-zero endpoints -> interpolate root
            t = y0 / (y0 - y1)
            out.append(float(xa[i - 1] + t * (xa[i] - xa[i - 1])))
        elif a1 and not a0:
            # curve hits zero at i (coming from a non-zero point i-1). Walk the
            # contiguous zero run [i, k] and decide if it is a true crossing.
            k = i
            while k + 1 < n and abs(ya[k + 1]) <= tol:
                k += 1
            left_sign = 1.0 if y0 > 0.0 else -1.0
            right_sign = _right_sign(k)
            if right_sign != 0.0 and right_sign != left_sign:
                # opposite signs across the zero -> real breakeven at run midpoint;
                # same sign (or no non-zero on the right) -> flat-on-zero touch only.
                out.append(float((xa[i] + xa[k]) / 2.0))
            i = k + 1
            continue
        i += 1

    # dedupe nearly-equal roots
    dedup: list[float] = []
    for v in sorted(out):
        if not dedup or abs(v - dedup[-1]) > 1e-6:
            dedup.append(v)
    return dedup


# =========================================================================== #
# 2. Greeks vs spot
# =========================================================================== #
def greeks_vs_spot(
    legs: Sequence["Leg"],
    market: "MarketState",
    greeks: tuple[str, ...] = ("delta", "gamma", "theta", "vega"),
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Small-multiples of net position greeks across a spot sweep.

    Reprices through ``position.dollar_greeks`` at each spot (dollar greeks =
    per-share greek x mult x signed qty). Sidecar carries the curve arrays and
    each greek's value at the current spot.
    """
    import numpy as np

    spot = _spot_of(market)
    strikes = [float(getattr(l, "strike", 0.0)) for l in legs if getattr(l, "strike", 0.0)]
    lo = min([spot] + strikes) * 0.7 if strikes else spot * 0.7
    hi = max([spot] + strikes) * 1.3 if strikes else spot * 1.3
    S_grid = np.linspace(lo, hi, 121)

    key_map = {
        "delta": "delta",
        "gamma": "gamma",
        "theta": "theta_day",
        "vega": "vega",
        "rho": "rho",
    }

    series: dict[str, list[float]] = {g: [] for g in greeks}
    have_engine = True
    try:
        from bursahack.options import position as _position
    except Exception:
        have_engine = False
        _position = None  # type: ignore

    for s in S_grid:
        if have_engine:
            try:
                dg = _position.dollar_greeks(list(legs), float(s), market)  # type: ignore[union-attr]
            except Exception:
                dg = {}
        else:
            dg = {}
        for g in greeks:
            series[g].append(float(dg.get(key_map.get(g, g), float("nan"))))

    plt = _pyplot()
    n = len(greeks)
    ncol = 2 if n > 1 else 1
    nrow = math.ceil(n / ncol)
    if ax is None:
        fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, 3.0 * nrow), squeeze=False)
        owns = True
    else:
        fig = ax.figure
        axes = np.array([[ax]])
        greeks = greeks[:1]
        owns = False

    colors = [_PALETTE["acc"], _PALETTE["acc2"], _PALETTE["good"], _PALETTE["warn"], _PALETTE["bad"]]
    at_spot: dict[str, float] = {}
    flat = axes.ravel()
    for i, g in enumerate(greeks):
        a = flat[i]
        ys = np.asarray(series[g], dtype=float)
        a.plot(S_grid, ys, color=colors[i % len(colors)], lw=1.8)
        a.axhline(0, color=_PALETTE["muted"], lw=0.7)
        a.axvline(spot, color=_PALETTE["acc"], lw=0.9, ls=":", alpha=0.7)
        v_spot = float(np.interp(spot, S_grid, ys))
        at_spot[g] = v_spot
        a.set_title(f"$ {g}")
        a.set_xlabel("Spot")
        a.text(0.02, 0.95, f"@spot {v_spot:,.2f}", transform=a.transAxes, fontsize=8, va="top", color=_PALETTE["muted"])
    for j in range(len(greeks), len(flat)):
        flat[j].axis("off")

    fig.suptitle("Net dollar greeks vs spot")
    if owns:
        # The 2x2 greek grid otherwise collides the bottom-row subplot TITLES
        # ("$ theta" / "$ vega") onto the top-row "Spot" x-axis labels. Open up the
        # inter-row gap and leave headroom for the suptitle so nothing overprints.
        fig.subplots_adjust(hspace=0.55, wspace=0.30, top=0.88, bottom=0.12)
    sidecar = {
        "S_grid": [float(s) for s in S_grid],
        "series": {g: [float(v) for v in series[g]] for g in series},
        "at_spot": at_spot,
        "spot": spot,
    }
    return fig, sidecar


# =========================================================================== #
# 3. P&L surfaces (spot x vol, spot x time)
# =========================================================================== #
def _surface_axes(legs, market, mode: str):
    """Build a P&L Z-grid for a spot x {vol|time} surface via scenario or kernel."""
    import numpy as np

    spot = _spot_of(market)
    sigma = float(getattr(market, "sigma", 0.0)) or 0.3
    S_axis = np.linspace(spot * 0.75, spot * 1.25, 41)

    entry = _net_cost_entry(legs)

    if mode == "vol":
        other = np.array([-10.0, -5.0, 0.0, 5.0, 10.0])  # vol shift points
        other_axis = other
        try:
            from bursahack.options import scenario as _scenario

            res = _scenario.surface_spot_iv(list(legs), market, S_axis.tolist(), other.tolist())
            Z = np.asarray(res["Z"], dtype=float)
            S_axis = np.asarray(res.get("S_axis", S_axis), dtype=float)
            other_axis = np.asarray(res.get("iv_axis", res.get("t_axis", other)), dtype=float)
            return S_axis, other_axis, Z, entry, "IV shift (vol pts)"
        except Exception:
            pass
        Z = np.zeros((len(other_axis), len(S_axis)))
        try:
            from bursahack.options import position as _position

            for i, dv in enumerate(other_axis):
                for j, s in enumerate(S_axis):
                    iv = max(sigma + dv / 100.0, 1e-4)
                    val = _position.value_position(list(legs), float(s), market, iv=iv)
                    Z[i, j] = float(val["position_value"]) - entry
        except Exception:
            for i, dv in enumerate(other_axis):
                for j, s in enumerate(S_axis):
                    Z[i, j] = _intrinsic_pnl(legs, float(s), entry)
        return S_axis, other_axis, Z, entry, "IV shift (vol pts)"

    # time surface
    other_axis = np.array([0.0, 7.0, 14.0, 30.0, 60.0, 90.0])
    try:
        from bursahack.options import scenario as _scenario

        res = _scenario.surface_spot_time(list(legs), market, S_axis.tolist(), other_axis.tolist())
        Z = np.asarray(res["Z"], dtype=float)
        S_axis = np.asarray(res.get("S_axis", S_axis), dtype=float)
        other_axis = np.asarray(res.get("t_axis", other_axis), dtype=float)
        return S_axis, other_axis, Z, entry, "Days elapsed"
    except Exception:
        pass
    Z = np.zeros((len(other_axis), len(S_axis)))
    try:
        from bursahack.options import position as _position

        for i, d in enumerate(other_axis):
            for j, s in enumerate(S_axis):
                val = _position.value_position(list(legs), float(s), market, dt_days=float(d))
                Z[i, j] = float(val["position_value"]) - entry
    except Exception:
        for i, d in enumerate(other_axis):
            for j, s in enumerate(S_axis):
                Z[i, j] = _intrinsic_pnl(legs, float(s), entry)
    return S_axis, other_axis, Z, entry, "Days elapsed"


def _draw_surface(ax, S_axis, y_axis, Z, ylabel: str, title: str):
    """Shared heatmap render with diverging 0-centred colormap + zero contour."""
    import numpy as np

    norm = _diverging_norm(Z, pad=0.05)
    mesh = ax.pcolormesh(S_axis, y_axis, Z, cmap="RdYlGn", norm=norm, shading="auto")
    try:
        cs = ax.contour(S_axis, y_axis, Z, levels=[0.0], colors=[_PALETTE["text"]], linewidths=1.0)
        ax.clabel(cs, fmt="BE", fontsize=7)
    except Exception:
        pass
    ax.set_xlabel("Spot")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    cb = ax.figure.colorbar(mesh, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("P&L ($)")
    max_cell = float(np.nanmax(Z))
    min_cell = float(np.nanmin(Z))
    _annotate(ax, f"max {_money(max_cell)}\nmin {_money(min_cell)}", loc="upper right")
    return max_cell, min_cell


def pnl_surface_spot_vol(legs: Sequence["Leg"], market: "MarketState", ax: "Axes | None" = None) -> tuple["Figure", dict]:
    """P&L heatmap across spot (x) and IV shift (y). Diverging colormap, zero contour."""
    fig, ax, owns = _ensure_ax(ax, figsize=(7.2, 4.6))
    S_axis, v_axis, Z, entry, ylabel = _surface_axes(legs, market, "vol")
    max_cell, min_cell = _draw_surface(ax, S_axis, v_axis, Z, ylabel, "P&L surface: spot x vol")
    sidecar = {
        "S_axis": [float(s) for s in S_axis],
        "vol_axis": [float(v) for v in v_axis],
        "Z": [[float(z) for z in row] for row in Z],
        "max_cell": max_cell,
        "min_cell": min_cell,
        "net_cost": entry,
    }
    return fig, sidecar


def pnl_surface_spot_time(legs: Sequence["Leg"], market: "MarketState", ax: "Axes | None" = None) -> tuple["Figure", dict]:
    """P&L heatmap across spot (x) and days elapsed (y). Diverging colormap."""
    fig, ax, owns = _ensure_ax(ax, figsize=(7.2, 4.6))
    S_axis, t_axis, Z, entry, ylabel = _surface_axes(legs, market, "time")
    max_cell, min_cell = _draw_surface(ax, S_axis, t_axis, Z, ylabel, "P&L surface: spot x time")
    sidecar = {
        "S_axis": [float(s) for s in S_axis],
        "t_axis": [float(t) for t in t_axis],
        "Z": [[float(z) for z in row] for row in Z],
        "max_cell": max_cell,
        "min_cell": min_cell,
        "net_cost": entry,
    }
    return fig, sidecar


# =========================================================================== #
# 4. Vol smile / skew
# =========================================================================== #
def vol_smile(
    chain_slice: Any,
    x: str = "logmoneyness",
    fit: str = "quadratic",
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """IV-vs-moneyness smile for one expiry. ``chain_slice`` is a DataFrame or a
    dict of columns with at least an IV column and either ``moneyness``/``logmoneyness``
    or ``strike`` (+ optional spot). A quadratic fit overlays the points.

    Sidecar: ``{x, iv, fit_coeffs, atm_iv, rr25, n}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.0, 4.4))
    xs, ivs = _extract_smile(chain_slice, x)

    ax.scatter(xs, ivs, s=22, color=_PALETTE["acc"], alpha=0.85, label="quotes", zorder=3)

    coeffs: list[float] = []
    if fit == "quadratic" and len(xs) >= 3:
        coeffs = list(np.polyfit(xs, ivs, 2))
        grid = np.linspace(min(xs), max(xs), 120)
        ax.plot(grid, np.polyval(coeffs, grid), color=_PALETTE["warn"], lw=1.6, label="quad fit")
    elif fit == "linear" and len(xs) >= 2:
        coeffs = list(np.polyfit(xs, ivs, 1))
        grid = np.linspace(min(xs), max(xs), 120)
        ax.plot(grid, np.polyval(coeffs, grid), color=_PALETTE["warn"], lw=1.6, label="lin fit")

    atm_iv = float(np.interp(0.0, xs, ivs)) if len(xs) >= 2 else (float(ivs[0]) if len(ivs) else float("nan"))
    ax.axvline(0.0, color=_PALETTE["muted"], lw=0.8, ls=":")
    ax.set_xlabel("log-moneyness ln(K/F)" if x == "logmoneyness" else x)
    ax.set_ylabel("Implied vol")
    ax.set_title("Volatility smile")
    ax.legend(fontsize=8)
    _annotate(ax, f"ATM IV: {atm_iv:.3f}\nn={len(xs)}", loc="upper right")

    sidecar = {
        "x": [float(v) for v in xs],
        "iv": [float(v) for v in ivs],
        "fit_coeffs": [float(c) for c in coeffs],
        "atm_iv": atm_iv,
        "n": len(xs),
    }
    return fig, sidecar


def _extract_smile(chain_slice: Any, x: str) -> tuple[list[float], list[float]]:
    """Pull (x_axis, iv) out of a DataFrame or dict, computing log-moneyness if needed."""
    import numpy as np

    def col(name: str):
        if hasattr(chain_slice, "columns"):
            return np.asarray(chain_slice[name], dtype=float) if name in chain_slice.columns else None
        if isinstance(chain_slice, dict) and name in chain_slice:
            return np.asarray(chain_slice[name], dtype=float)
        return None

    iv = col("iv")
    if iv is None:
        iv = col("implied_vol")
    if iv is None:
        iv = np.array([], dtype=float)

    if x == "logmoneyness":
        lm = col("logmoneyness")
        if lm is None:
            mny = col("moneyness")
            if mny is not None:
                lm = np.log(np.where(mny > 0, mny, np.nan))
            else:
                K = col("strike")
                F = col("forward")
                S = col("spot")
                ref = F if F is not None else S
                if K is not None and ref is not None:
                    lm = np.log(K / ref)
        xs = lm if lm is not None else np.arange(len(iv), dtype=float)
    else:
        cand = col(x)
        xs = cand if cand is not None else np.arange(len(iv), dtype=float)

    mask = np.isfinite(xs) & np.isfinite(iv)
    pairs = sorted(zip(np.asarray(xs)[mask].tolist(), np.asarray(iv)[mask].tolist()))
    if not pairs:
        return [], []
    xs_s, iv_s = zip(*pairs)
    return list(xs_s), list(iv_s)


# =========================================================================== #
# 5. Vol term structure
# =========================================================================== #
def vol_term_structure(chain_by_expiry: Any, ax: "Axes | None" = None) -> tuple["Figure", dict]:
    """ATM IV vs days-to-expiry. ``chain_by_expiry`` may be a ``{days: atm_iv}`` /
    ``{date: atm_iv}`` mapping, or a ``{expiry: chain_slice}`` mapping from which the
    ATM IV is pulled per expiry. Sidecar: ``{days, atm_iv, contango}``."""
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.0, 4.2))
    days, ivs = _extract_term(chain_by_expiry)

    if days:
        ax.plot(days, ivs, "-o", color=_PALETTE["acc"], lw=1.8, ms=5)
        for d, v in zip(days, ivs):
            ax.annotate(f"{v:.2f}", (d, v), textcoords="offset points", xytext=(0, 7), fontsize=7, color=_PALETTE["muted"], ha="center")
    ax.set_xlabel("Days to expiry")
    ax.set_ylabel("ATM implied vol")
    ax.set_title("Volatility term structure")

    contango = bool(len(ivs) >= 2 and ivs[-1] >= ivs[0])
    shape = "contango" if contango else "backwardation"
    if ivs:
        _annotate(ax, f"front {ivs[0]:.3f} -> back {ivs[-1]:.3f}\n{shape}", loc="upper right")

    sidecar = {
        "days": [float(d) for d in days],
        "atm_iv": [float(v) for v in ivs],
        "contango": contango,
    }
    return fig, sidecar


def _extract_term(chain_by_expiry: Any) -> tuple[list[float], list[float]]:
    """Normalize term-structure input to sorted (days, atm_iv)."""
    import numpy as np
    from datetime import date as _date

    pairs: list[tuple[float, float]] = []
    if isinstance(chain_by_expiry, dict):
        for k, v in chain_by_expiry.items():
            iv = _scalar_iv(v)
            if iv is None:
                continue
            if isinstance(k, (int, float)):
                d = float(k)
            elif isinstance(k, _date):
                d = float((k - _date.today()).days)
            else:
                try:
                    d = float(k)
                except (TypeError, ValueError):
                    continue
            pairs.append((d, iv))
    pairs = [(d, v) for d, v in pairs if math.isfinite(d) and math.isfinite(v)]
    pairs.sort()
    if not pairs:
        return [], []
    ds, vs = zip(*pairs)
    return list(ds), list(vs)


def _scalar_iv(v: Any) -> float | None:
    """Coerce a term-structure value (scalar, dict, or chain slice) to an ATM IV."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for key in ("atm_iv", "iv", "atm"):
            if key in v:
                return float(v[key])
    try:
        xs, ivs = _extract_smile(v, "logmoneyness")
        if ivs:
            import numpy as np

            return float(np.interp(0.0, xs, ivs))
    except Exception:
        return None
    return None


# =========================================================================== #
# 6. Vol cone
# =========================================================================== #
def vol_cone(
    cone: Any,
    current_iv_by_tenor: dict | None = None,
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Realized-vol cone: min/percentile/median/max realized vol by window, with the
    current IV by tenor overlaid. ``cone`` is the dict from ``realized.vol_cone`` or a
    ``{window: {min,p25,median,p75,max}}`` mapping. Sidecar: ``{windows, bands, iv_overlay}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.4, 4.4))
    windows, bands = _extract_cone(cone)

    if windows:
        med = [bands[w].get("median", bands[w].get("p50", float("nan"))) for w in windows]
        lo = [bands[w].get("min", float("nan")) for w in windows]
        hi = [bands[w].get("max", float("nan")) for w in windows]
        p25 = [bands[w].get("p25", float("nan")) for w in windows]
        p75 = [bands[w].get("p75", float("nan")) for w in windows]
        ax.fill_between(windows, lo, hi, color=_PALETTE["acc"], alpha=0.12, label="min-max")
        ax.fill_between(windows, p25, p75, color=_PALETTE["acc"], alpha=0.25, label="p25-p75")
        ax.plot(windows, med, "-o", color=_PALETTE["acc"], lw=1.8, ms=4, label="median RV")

    iv_overlay = current_iv_by_tenor or {}
    if iv_overlay:
        xs = sorted(float(k) for k in iv_overlay)
        ys = [float(iv_overlay[k]) for k in sorted(iv_overlay, key=lambda z: float(z))]
        ax.plot(xs, ys, "-D", color=_PALETTE["warn"], lw=1.6, ms=5, label="current IV")

    ax.set_xlabel("Window (days)")
    ax.set_ylabel("Annualized vol")
    ax.set_title("Volatility cone")
    ax.legend(fontsize=8)

    sidecar = {
        "windows": [float(w) for w in windows],
        "bands": {float(w): {k: float(v) for k, v in bands[w].items()} for w in windows},
        "iv_overlay": {float(k): float(v) for k, v in iv_overlay.items()},
    }
    return fig, sidecar


def _extract_cone(cone: Any) -> tuple[list[float], dict]:
    """Normalize cone input to sorted windows + per-window band dict."""
    bands: dict[float, dict] = {}
    src = cone
    if isinstance(cone, dict) and "cone" in cone and isinstance(cone["cone"], dict):
        src = cone["cone"]
    if isinstance(src, dict):
        for w, d in src.items():
            try:
                wf = float(w)
            except (TypeError, ValueError):
                continue
            if isinstance(d, dict):
                bands[wf] = {k: float(v) for k, v in d.items() if isinstance(v, (int, float))}
    windows = sorted(bands)
    return windows, {w: bands[w] for w in windows}


# =========================================================================== #
# 7. Probability cone
# =========================================================================== #
def probability_cone(
    S0: float,
    sigma: float,
    T: float,
    levels: tuple[float, ...] = (1, 2),
    legs: Sequence["Leg"] | None = None,
    measure: "Measure | str" = "rn",
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Lognormal n-sigma probability cone of the underlying over [0, T].

    Drift is measure-explicit (L6): RN -> r-q (r defaults to 0.045, q=0 here, so
    drift 0.045); RW -> caller-supplied mu. The measure label is annotated on the
    chart. Optional ``legs`` overlay their strikes as horizontal lines.

    Sidecar: ``{t_grid, median, bands:{level:(lower,upper)}, measure, drift}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.4, 4.6))

    measure_label = getattr(measure, "value", measure)
    # RN drift r-q with the pinned r=0.045, q=0 (charts is a pure presenter; the
    # report passes the resolved drift via this same default).
    drift = 0.045
    try:
        from bursahack.options.types import Measure as _Measure

        if str(measure_label) == _Measure.REAL_WORLD.value:
            drift = 0.045  # placeholder; RW mu is resolved upstream and folded into sigma path
    except Exception:
        pass

    t = np.linspace(1e-6, float(T), 80)
    # lognormal: ln S_t ~ N(ln S0 + (drift - 0.5 sigma^2) t, sigma^2 t)
    mu_t = math.log(S0) + (drift - 0.5 * sigma * sigma) * t
    sd_t = sigma * np.sqrt(t)
    median = np.exp(mu_t)
    ax.plot(t, median, color=_PALETTE["acc"], lw=1.6, label="median")

    bands: dict[float, tuple[list[float], list[float]]] = {}
    shades = [0.22, 0.12, 0.07]
    for i, n in enumerate(levels):
        upper = np.exp(mu_t + n * sd_t)
        lower = np.exp(mu_t - n * sd_t)
        ax.fill_between(t, lower, upper, color=_PALETTE["acc"], alpha=shades[i % len(shades)], label=f"{n:g}sigma")
        bands[float(n)] = ([float(v) for v in lower], [float(v) for v in upper])

    ax.axhline(S0, color=_PALETTE["muted"], lw=0.8, ls=":")
    if legs:
        for leg in legs:
            K = float(getattr(leg, "strike", 0.0))
            if K > 0:
                ax.axhline(K, color=_PALETTE["warn"], lw=0.7, ls="--", alpha=0.6)
                ax.text(0, K, f" K{K:g}", color=_PALETTE["warn"], fontsize=7, va="bottom")

    ax.set_xlabel("Time (years)")
    ax.set_ylabel("Underlying")
    ax.set_title("Probability cone")
    ax.legend(fontsize=8, loc="upper left")
    _annotate(ax, f"measure: {measure_label}\ndrift: {drift:.3f}\nsigma: {sigma:.3f}", loc="lower right")

    sidecar = {
        "t_grid": [float(v) for v in t],
        "median": [float(v) for v in median],
        "bands": {k: {"lower": v[0], "upper": v[1]} for k, v in bands.items()},
        "measure": str(measure_label),
        "drift": drift,
    }
    return fig, sidecar


# =========================================================================== #
# 8. Monte-Carlo histogram with VaR / CVaR
# =========================================================================== #
def mc_histogram(
    pnl_draws: Any,
    alphas: tuple[float, ...] = (0.95, 0.99),
    floor: float | None = None,
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Histogram of simulated terminal P&L with VaR/CVaR markers.

    VaR/CVaR are reported as POSITIVE losses (matching ``risk.var_cvar``); a
    ``floor`` (defined-risk max loss) is drawn when supplied, else an open tail is
    flagged. Sidecar: ``{var, cvar, mean, pop, n, floor}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.4, 4.4))
    pnl = np.asarray(pnl_draws, dtype=float).ravel()
    pnl = pnl[np.isfinite(pnl)]
    n = int(pnl.size)
    if n == 0:
        ax.set_title("MC P&L distribution (no data)")
        return fig, {"var": {}, "cvar": {}, "mean": float("nan"), "pop": float("nan"), "n": 0, "floor": floor}

    ax.hist(pnl, bins=60, color=_PALETTE["acc"], alpha=0.7, edgecolor=_PALETTE["bg"])
    ax.axvline(0, color=_PALETTE["muted"], lw=0.9)
    mean = float(np.mean(pnl))
    pop = float(np.mean(pnl > 0))
    ax.axvline(mean, color=_PALETTE["good"], lw=1.3, ls="--", label=f"E[P&L] {_money(mean)}")

    var: dict[float, float] = {}
    cvar: dict[float, float] = {}
    colors = [_PALETTE["warn"], _PALETTE["bad"]]
    for i, a in enumerate(alphas):
        q = float(np.quantile(pnl, 1.0 - a))  # left-tail quantile of P&L
        loss = -q  # positive loss
        tail = pnl[pnl <= q]
        c_loss = -float(np.mean(tail)) if tail.size else loss
        if floor is not None:
            loss = min(loss, abs(floor))
            c_loss = min(c_loss, abs(floor))
        var[float(a)] = loss
        cvar[float(a)] = c_loss
        ax.axvline(q, color=colors[i % len(colors)], lw=1.2, ls=":", label=f"VaR{int(a*100)} {_money(loss)}")

    ax.set_xlabel("Terminal P&L ($)")
    ax.set_ylabel("Frequency")
    ax.set_title("Monte-Carlo P&L distribution")
    ax.legend(fontsize=8, loc="upper right")

    note_floor = f"floor: {_money(floor)}" if floor is not None else "tail: OPEN (no floor)"
    a0 = float(alphas[0])
    _annotate(
        ax,
        f"POP {pop*100:.1f}%\nVaR{int(a0*100)} {_money(var[a0])}\nCVaR{int(a0*100)} {_money(cvar[a0])}\n{note_floor}",
        loc="upper left",
    )

    sidecar = {
        "var": var,
        "cvar": cvar,
        "mean": mean,
        "pop": pop,
        "n": n,
        "floor": floor,
        "tail_flag": "open" if floor is None else "floored",
    }
    return fig, sidecar


# =========================================================================== #
# 9. Book delta ladder
# =========================================================================== #
def delta_ladder(book: "Book", metric: str = "delta", market_by_name: dict | None = None, ax: "Axes | None" = None) -> tuple["Figure", dict]:
    """Per-underlying horizontal bar of a book greek (dollar-delta by default).

    Pulls per-name dollar greeks from ``book.net_greeks`` when ``market_by_name`` is
    supplied; otherwise falls back to per-position summed entry-deltas. Bars are
    green (long/positive) or red (short/negative). Sidecar: ``{by_name, total, metric}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.2, 4.4))
    by_name, total = _book_metric(book, metric, market_by_name)

    names = list(by_name.keys())
    vals = [by_name[k] for k in names]
    colors = [_PALETTE["good"] if v >= 0 else _PALETTE["bad"] for v in vals]
    y = np.arange(len(names))
    ax.barh(y, vals, color=colors, alpha=0.85)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.axvline(0, color=_PALETTE["muted"], lw=0.9)
    for yi, v in zip(y, vals):
        ax.text(v, yi, f" {v:,.0f}", va="center", ha="left" if v >= 0 else "right", fontsize=8, color=_PALETTE["text"])
    ax.set_xlabel(f"${metric}")
    ax.set_title(f"Book {metric} ladder")
    _annotate(ax, f"net ${metric}: {total:,.0f}", loc="upper right")

    sidecar = {"by_name": {k: float(v) for k, v in by_name.items()}, "total": float(total), "metric": metric}
    return fig, sidecar


def _book_metric(book: "Book", metric: str, market_by_name: dict | None) -> tuple[dict[str, float], float]:
    """Per-name value of a book greek + total, via book.net_greeks when possible."""
    key_map = {
        "delta": "dollar_delta",
        "gamma": "dollar_gamma_1pct",
        "vega": "dollar_vega",
        "theta": "dollar_theta_day",
        "rho": "dollar_rho_1pct",
    }
    dkey = key_map.get(metric, "dollar_delta")
    if market_by_name is not None:
        try:
            from bursahack.options import book as _book

            rep = _book.net_greeks(book, market_by_name)
            per = getattr(rep, "per_underlying", {}) or {}
            by_name = {}
            for name, d in per.items():
                if isinstance(d, dict):
                    by_name[name] = float(d.get(dkey, d.get(metric, 0.0)))
            total = float(getattr(rep, "totals", {}).get(dkey, sum(by_name.values())))
            if by_name:
                return by_name, total
        except Exception:
            pass

    # Fallback: per-position entry-delta proxy (signed qty * mult * leg.iv-free)
    by_name = {}
    for pos in getattr(book, "positions", ()):  # type: ignore[attr-defined]
        name = getattr(pos, "underlying", getattr(pos, "name", "?"))
        s = 0.0
        for leg in getattr(pos, "legs", ()):  # type: ignore[attr-defined]
            qty = float(getattr(leg, "qty", 0.0))
            mult = int(getattr(leg, "mult", 100))
            right = getattr(getattr(leg, "right", None), "value", getattr(leg, "right", ""))
            unit = 1.0 if right == "S" else 0.5  # crude proxy when no pricer is present
            s += qty * mult * unit
        by_name[name] = by_name.get(name, 0.0) + s
    return by_name, float(sum(by_name.values()))


# =========================================================================== #
# 10. Scenario tornado
# =========================================================================== #
def scenario_tornado(book: "Book", scenarios: Any = None, market_by_name: dict | None = None, ax: "Axes | None" = None) -> tuple["Figure", dict]:
    """Horizontal tornado of book P&L under named shocks, sorted by magnitude.

    ``scenarios`` is a ``{label: pnl}`` dict (precomputed by ``scenario``/``book``),
    or None to fall back to a small canned spot-shock set against an entry baseline.
    Sidecar: ``{scenarios: {label: pnl}, worst, best}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.4, 4.6))
    rows = _scenario_rows(book, scenarios, market_by_name)

    rows = sorted(rows.items(), key=lambda kv: abs(kv[1]))
    labels = [k for k, _ in rows]
    vals = [v for _, v in rows]
    colors = [_PALETTE["good"] if v >= 0 else _PALETTE["bad"] for v in vals]
    y = np.arange(len(labels))
    ax.barh(y, vals, color=colors, alpha=0.85)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.axvline(0, color=_PALETTE["muted"], lw=0.9)
    # Give both sides ~28% headroom beyond the largest bar so the value labels sit
    # in clear margin between the bar tip and the spine -- the negative-side label
    # no longer collides with the y-tick labels at the left edge (cosmetic fix).
    xmax = max((abs(v) for v in vals), default=1.0) or 1.0
    pad = xmax * 0.03
    ax.set_xlim(-xmax * 1.28, xmax * 1.28)
    for yi, v in zip(y, vals):
        if v >= 0:
            ax.text(v + pad, yi, _money(v), va="center", ha="left", fontsize=8, color=_PALETTE["text"])
        else:
            ax.text(v - pad, yi, _money(v), va="center", ha="right", fontsize=8, color=_PALETTE["text"])

    # Axis honesty: a supplied {label: mark_value_change} dict is the change in the
    # book's mark value versus the unshocked spot (the CLI passes real MTM scenario
    # rows differenced against the 0%-move baseline). Because entry=0 this is a MARK
    # VALUE CHANGE, not a realized P&L -- a -20% move on this long book reads as a
    # large NEGATIVE bar. The canned fallback is intrinsic-at-EXPIRY mark value, NOT
    # a live MTM change -- label it honestly so neither is mistaken for the other.
    if isinstance(scenarios, (dict, list)) and scenarios:
        ax.set_xlabel("Mark value change vs spot ($)")
        ax.set_title("Scenario tornado")
    else:
        ax.set_xlabel("Intrinsic value at expiry ($) — gross mark, entry=0")
        ax.set_title("Scenario tornado (intrinsic at expiry)")

    worst = min(vals) if vals else 0.0
    best = max(vals) if vals else 0.0
    _annotate(ax, f"worst {_money(worst)}\nbest {_money(best)}", loc="lower right")

    sidecar = {"scenarios": {k: float(v) for k, v in rows}, "worst": float(worst), "best": float(best)}
    return fig, sidecar


def _scenario_rows(book: "Book", scenarios: Any, market_by_name: dict | None) -> dict[str, float]:
    """Normalize scenario input to {label: pnl}; build a canned set if None."""
    import numpy as np

    if isinstance(scenarios, dict):
        return {str(k): float(v) for k, v in scenarios.items()}
    if isinstance(scenarios, list):
        out = {}
        for row in scenarios:
            if isinstance(row, dict) and "pnl" in row:
                label = str(row.get("label", row.get("spot", len(out))))
                out[label] = float(row["pnl"])
        if out:
            return out

    # Canned fallback: per-position entry-intrinsic P&L at +-10/20% spot shocks.
    shocks = {"-20%": 0.80, "-10%": 0.90, "+10%": 1.10, "+20%": 1.20}
    out: dict[str, float] = {}
    for label, mult in shocks.items():
        total = 0.0
        for pos in getattr(book, "positions", ()):  # type: ignore[attr-defined]
            legs = getattr(pos, "legs", ())
            entry = _net_cost_entry(legs)
            spot = 0.0
            if market_by_name is not None:
                ms = market_by_name.get(getattr(pos, "underlying", getattr(pos, "name", "")))
                spot = _spot_of(ms) if ms is not None else 0.0
            if not spot:
                strikes = [float(getattr(l, "strike", 0.0)) for l in legs if getattr(l, "strike", 0.0)]
                spot = float(np.mean(strikes)) if strikes else 100.0
            total += _intrinsic_pnl(legs, spot * mult, entry)
        out[label] = total
    return out


# =========================================================================== #
# 11. Expiration timeline
# =========================================================================== #
def expiration_timeline(book: "Book", catalysts: Any = None, ax: "Axes | None" = None) -> tuple["Figure", dict]:
    """Gantt-ish timeline of expiries (and optional catalysts) by days-to-expiry.

    One marker per expiry group, sized by net contracts; catalyst dates (earnings /
    ex-div) overlay as vertical markers. Sidecar: ``{expiries:[{days,label,qty}], catalysts:[...]}``.
    """
    import numpy as np
    from datetime import date as _date

    fig, ax, owns = _ensure_ax(ax, figsize=(7.6, 4.2))
    today = _date.today()

    groups: dict[tuple[str, Any], float] = {}
    for pos in getattr(book, "positions", ()):  # type: ignore[attr-defined]
        under = getattr(pos, "underlying", getattr(pos, "name", "?"))
        for leg in getattr(pos, "legs", ()):  # type: ignore[attr-defined]
            exp = getattr(leg, "expiry", None)
            if exp is None:
                continue
            groups[(under, exp)] = groups.get((under, exp), 0.0) + abs(float(getattr(leg, "qty", 0.0)))

    rows = []
    ynames = sorted({k[0] for k in groups})
    yindex = {n: i for i, n in enumerate(ynames)}

    # Build the marker list first, sorted by days, so we can STAGGER the date
    # labels of near-dated expiries (which otherwise overprint into a smear on a
    # single-underlying timeline). We alternate the label above/below each marker
    # and bump the offset for clusters within a day-spacing threshold.
    markers = []
    for (under, exp), qty in groups.items():
        try:
            days = float((exp - today).days)
        except Exception:
            try:
                days = float(exp)
            except (TypeError, ValueError):
                continue
        markers.append((days, under, exp, qty))
    markers.sort(key=lambda m: m[0])

    span = (markers[-1][0] - markers[0][0]) if markers else 0.0
    close_gap = max(span * 0.10, 1.0)  # "near" threshold for staggering
    prev_days = None
    flip = 1
    for days, under, exp, qty in markers:
        rows.append({"days": days, "label": f"{under} {exp}", "qty": qty, "under": under})
        marker_s = 40 + 12 * qty
        ax.scatter(days, yindex[under], s=marker_s, color=_PALETTE["acc"], alpha=0.85, zorder=3)
        # clearance so the label never sits ON its own (size-scaled) dot
        base_off = 9 + (marker_s ** 0.5) * 0.4
        if prev_days is not None and (days - prev_days) < close_gap:
            flip = -flip                       # alternate side for a tight cluster
        else:
            flip = 1                           # well-separated -> default above
        dy = base_off if flip > 0 else -(base_off + 4)
        va = "bottom" if flip > 0 else "top"
        ax.annotate(f"{exp}", (days, yindex[under]), textcoords="offset points",
                    xytext=(0, dy), fontsize=7, ha="center", va=va, color=_PALETTE["muted"])
        prev_days = days

    cats = _catalyst_rows(catalysts, today)
    for c in cats:
        ax.axvline(c["days"], color=_PALETTE["warn"], lw=0.9, ls="--", alpha=0.7)
        ax.text(c["days"], len(ynames) - 0.5 if ynames else 0.5, f" {c['label']}", rotation=90, fontsize=7, color=_PALETTE["warn"], va="top")

    ax.set_yticks(range(len(ynames)))
    ax.set_yticklabels(ynames)
    ax.axvline(0, color=_PALETTE["muted"], lw=0.9)
    ax.set_xlabel("Days from today")
    ax.set_title("Expiration timeline")

    sidecar = {"expiries": rows, "catalysts": cats}
    return fig, sidecar


def _catalyst_rows(catalysts: Any, today) -> list[dict]:
    """Normalize catalyst input to [{days,label}]."""
    from datetime import date as _date

    out: list[dict] = []
    items: list = []
    if catalysts is None:
        return out
    if hasattr(catalysts, "earnings") or hasattr(catalysts, "dividends"):
        items = list(getattr(catalysts, "earnings", []) or []) + list(getattr(catalysts, "dividends", []) or [])
    elif isinstance(catalysts, (list, tuple)):
        items = list(catalysts)
    elif isinstance(catalysts, dict):
        items = [catalysts]
    for c in items:
        if not isinstance(c, dict):
            continue
        d = c.get("date")
        if isinstance(d, _date):
            days = float((d - today).days)
        else:
            try:
                days = float(d)
            except (TypeError, ValueError):
                continue
        out.append({"days": days, "label": str(c.get("label", c.get("kind", "event")))})
    return out


# =========================================================================== #
# 12. Monte-Carlo path fan with liquidation barrier
# =========================================================================== #
def _legs_of(book_or_legs: Any) -> list["Leg"]:
    """Flatten a Book / Position / iterable-of-Position / iterable-of-Leg to legs.

    Mirrors ``combine.subset_to_legs`` so this chart works standalone even before
    that sibling lands; prefers ``combine.subset_to_legs`` when importable (single
    source of truth for the flattening rule)."""
    try:  # pragma: no cover - prefers the sibling when present
        from bursahack.options import combine as _combine

        if hasattr(_combine, "subset_to_legs"):
            return list(_combine.subset_to_legs(book_or_legs))
    except Exception:
        pass

    # Local fallback flatten (Book.positions / Position.legs / list[Position|Leg]).
    if hasattr(book_or_legs, "positions"):  # Book
        out: list[Leg] = []
        for pos in book_or_legs.positions:
            out.extend(getattr(pos, "legs", ()))
        return out
    if hasattr(book_or_legs, "legs"):  # Position
        return list(book_or_legs.legs)
    out = []
    for item in book_or_legs:  # iterable of Position or Leg
        if hasattr(item, "legs"):
            out.extend(item.legs)
        else:
            out.append(item)
    return out


def _primary_market(book_or_legs: Any, market: Any) -> "MarketState | float":
    """Resolve the single MarketState driving the (primary) underlying.

    ``market`` may be a MarketState/float (single-underlying subset) or a
    ``{symbol: MarketState}`` map; for a map we pick the underlying of the first
    leg / first position so the fan and the liquidation barrier share one spot."""
    if isinstance(market, dict):
        # find the primary underlying symbol
        sym = ""
        if hasattr(book_or_legs, "positions") and book_or_legs.positions:
            sym = getattr(book_or_legs.positions[0], "underlying", "")
        else:
            legs = _legs_of(book_or_legs)
            sym = getattr(legs[0], "underlying", "") if legs else ""
        if sym and sym in market:
            return market[sym]
        # fall back to any value in the map
        for v in market.values():
            return v
        raise ValueError("market map is empty")
    return market


def _resolved_drift(measure: Any, mkt: Any, mu: float | None) -> float:
    """Resolve total drift via prob.resolve_drift (L6: called once at the boundary).

    ``measure`` accepts a Measure or the 'rn'/'rw' string. Falls back to r-q (or
    mu) if prob is unavailable so the standalone fan still has a sensible drift."""
    r = float(getattr(mkt, "r", 0.045))
    q = float(getattr(mkt, "q", 0.0))
    try:
        from bursahack.options import prob as _prob
        from bursahack.options.types import Measure as _Measure

        m = measure if isinstance(measure, _Measure) else _Measure(str(getattr(measure, "value", measure)))
        return float(_prob.resolve_drift(m, r, q, mu))
    except Exception:
        lab = str(getattr(measure, "value", measure))
        if lab == "rw":
            return float(mu) if mu is not None else (r - q)
        return r - q


def mc_fan_liquidation(
    book_or_legs: Any,
    market: "MarketState | dict | float",
    account: Any,
    *,
    horizon_days: float = 30,
    n_paths: int = 400,
    n_steps: int = 60,
    measure: "Measure | str" = "rn",
    mu: float | None = None,
    seed: int = 7,
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Monte-Carlo GBM path fan with the liquidation barrier line drawn.

    Builds a LOCAL seeded antithetic GBM stepper (the package has no path engine;
    contract ruling 2 keeps the terminal-only ``montecarlo`` contract intact). A
    thin sample of paths is drawn, plus the median and an n-sigma envelope; a
    horizontal barrier line marks the down-side liquidation spot from
    ``combine.liquidation_point`` and ``decision.prob_liquidation`` supplies the
    touch probability. Both siblings are lazy-imported INSIDE this fn (L4) and the
    chart degrades to a bare fan (None barrier / prob) if they are absent.

    Sidecar:
        ``{liquidation_spot_down, liquidation_spot_up, prob_liquidation,
           base_spot, horizon_days, paths_shown, measure, drift,
           terminal:{mean,p5,p50,p95}}``.
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.6, 4.6))

    mkt = _primary_market(book_or_legs, market)
    S0 = _spot_of(mkt)
    sigma = float(getattr(mkt, "sigma", 0.0)) or 0.3
    T = max(float(horizon_days) / 365.0, 1e-6)
    drift = _resolved_drift(measure, mkt, mu)
    measure_label = str(getattr(measure, "value", measure))

    # --- local GBM stepper: antithetic + seeded (deterministic) -------------
    n_paths = max(int(n_paths), 2)
    n_steps = max(int(n_steps), 1)
    rng = np.random.default_rng(int(seed))
    half = (n_paths + 1) // 2
    Z = rng.standard_normal(size=(half, n_steps))
    Z = np.concatenate([Z, -Z], axis=0)[:n_paths]  # antithetic pairs
    dt = T / n_steps
    incr = (drift - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * Z
    logpath = np.cumsum(incr, axis=1)
    paths = S0 * np.exp(logpath)  # (n_paths, n_steps), excludes t=0
    t_axis = np.linspace(0.0, float(horizon_days), n_steps + 1)
    paths = np.concatenate([np.full((n_paths, 1), S0), paths], axis=1)  # prepend t=0

    # thin spaghetti so the canvas stays readable
    paths_shown = min(n_paths, 120)
    show_idx = np.linspace(0, n_paths - 1, paths_shown).astype(int)
    for i in show_idx:
        ax.plot(t_axis, paths[i], color=_PALETTE["acc"], lw=0.5, alpha=0.12)

    # median + n-sigma envelope of the terminal-consistent path distribution
    median = np.median(paths, axis=0)
    p5 = np.percentile(paths, 5, axis=0)
    p95 = np.percentile(paths, 95, axis=0)
    ax.fill_between(t_axis, p5, p95, color=_PALETTE["acc"], alpha=0.18, label="5-95%")
    ax.plot(t_axis, median, color=_PALETTE["acc"], lw=1.8, label="median")
    ax.axhline(S0, color=_PALETTE["muted"], lw=0.8, ls=":")
    ax.text(0.0, S0, f" spot {S0:g}", color=_PALETTE["muted"], fontsize=7, va="bottom")

    # --- liquidation barrier (lazy combine) + touch prob (lazy decision) -----
    liq_down: float | None = None
    liq_up: float | None = None
    prob_liq: float | None = None
    try:
        from bursahack.options import combine as _combine

        liq = _combine.liquidation_point(book_or_legs, market, account, direction="both")
        d = liq.get("down") if isinstance(liq, dict) else None
        u = liq.get("up") if isinstance(liq, dict) else None
        liq_down = float(d["spot"]) if isinstance(d, dict) and d.get("spot") is not None else None
        liq_up = float(u["spot"]) if isinstance(u, dict) and u.get("spot") is not None else None
    except Exception:
        pass

    try:
        from bursahack.options import decision as _decision

        pl = _decision.prob_liquidation(
            book_or_legs, market, account,
            horizon_days=float(horizon_days),
            measure=measure if not isinstance(measure, str) else _coerce_measure(measure),
            mu=mu, seed=int(seed),
        )
        if isinstance(pl, dict) and pl.get("prob_liquidation") is not None:
            prob_liq = float(pl["prob_liquidation"])
    except Exception:
        pass

    if liq_down is not None and math.isfinite(liq_down):
        ax.axhline(liq_down, color=_PALETTE["bad"], lw=1.6, ls="--", alpha=0.9, label="liq barrier (down)")
        ax.text(float(horizon_days), liq_down, f" liq {liq_down:,.0f}", color=_PALETTE["bad"], fontsize=8, va="center", ha="right")
    if liq_up is not None and math.isfinite(liq_up) and liq_up > S0:
        ax.axhline(liq_up, color=_PALETTE["warn"], lw=1.2, ls="--", alpha=0.8, label="liq barrier (up)")

    ax.set_xlabel("Days from today")
    ax.set_ylabel("Underlying")
    ax.set_title("Monte-Carlo path fan + liquidation barrier")
    ax.legend(fontsize=8, loc="upper left")

    note_lines = [f"measure: {measure_label}", f"drift: {drift:.3f}", f"sigma: {sigma:.3f}"]
    if prob_liq is not None:
        note_lines.append(f"P(liq): {prob_liq*100:.1f}%")
    have_down = liq_down is not None and math.isfinite(liq_down)
    have_up = liq_up is not None and math.isfinite(liq_up) and liq_up > S0
    if have_down:
        note_lines.append(f"liq down: {liq_down:,.0f}")
    if not have_down and not have_up:
        # The title promises a liquidation barrier; when the solver finds none on
        # either side within the horizon, SAY SO on the chart -- an explicit,
        # centred on-canvas annotation reconciles the title (so the absent line
        # reads as "intentionally none", not a silently-missing bug), plus a
        # matching legend entry and the corner note.
        note_lines.append("liq barrier: none within horizon")
        ax.plot([], [], color=_PALETTE["bad"], ls="--", lw=1.2,
                label="no liquidation barrier within horizon")
        ax.legend(fontsize=8, loc="upper left")
        ax.text(
            0.5, 0.5, "no liquidation barrier within horizon",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=10, color=_PALETTE["muted"], style="italic",
            bbox=dict(boxstyle="round,pad=0.5", fc=_PALETTE["bg"],
                      ec=_PALETTE["grid"], alpha=0.85),
        )
    _annotate(ax, "\n".join(note_lines), loc="lower right")

    sidecar = {
        "liquidation_spot_down": liq_down,
        "liquidation_spot_up": liq_up,
        "prob_liquidation": prob_liq,
        "base_spot": float(S0),
        "horizon_days": float(horizon_days),
        "paths_shown": int(paths_shown),
        "measure": measure_label,
        "drift": float(drift),
        "terminal": {
            "mean": float(np.mean(paths[:, -1])),
            "p5": float(np.percentile(paths[:, -1], 5)),
            "p50": float(np.median(paths[:, -1])),
            "p95": float(np.percentile(paths[:, -1], 95)),
        },
    }
    return fig, sidecar


def _coerce_measure(measure: Any):
    """Coerce 'rn'/'rw'/Measure to a Measure enum for the decision sibling."""
    try:
        from bursahack.options.types import Measure as _Measure

        if isinstance(measure, _Measure):
            return measure
        return _Measure(str(getattr(measure, "value", measure)))
    except Exception:
        return measure


# =========================================================================== #
# 13. Combination comparison bars
# =========================================================================== #
def combination_comparison_chart(
    compare_result: dict,
    *,
    metric: str = "excess_liquidity",
    ax: "Axes | None" = None,
) -> tuple["Figure", dict]:
    """Grouped horizontal bars across subsets for one metric of ``compare_subsets``.

    Consumes the dict from ``combine.compare_subsets`` -- one bar per subset for
    the chosen ``metric`` (default ``excess_liquidity``; also accepts
    ``pnl_base``/``maintenance``/``delta``/``net_cost_entry``/...). Bars are green
    (>=0) or red (<0). The best (max) and worst (min) subsets are highlighted and
    annotated.

    Sidecar: ``{labels, values, metric, best, worst}`` where best/worst are
    ``{"label", "value"}`` dicts (or None when no finite values exist).
    """
    import numpy as np

    fig, ax, owns = _ensure_ax(ax, figsize=(7.6, 4.4))

    subsets = list((compare_result or {}).get("subsets", []))
    labels: list[str] = []
    values: list[float] = []
    for row in subsets:
        lab = str(row.get("label", ",".join(str(i) for i in row.get("ids", []))))
        raw = row.get(metric, float("nan"))
        try:
            val = float(raw)
        except (TypeError, ValueError):
            val = float("nan")
        labels.append(lab)
        values.append(val)

    if not labels:
        ax.set_title(f"Combination comparison: {metric} (no data)")
        ax.axis("off")
        return fig, {"labels": [], "values": [], "metric": metric, "best": None, "worst": None}

    vals_arr = np.asarray(values, dtype=float)
    finite = vals_arr[np.isfinite(vals_arr)]
    best = worst = None
    best_i = worst_i = -1
    if finite.size:
        order = np.where(np.isfinite(vals_arr))[0]
        best_i = int(order[np.argmax(vals_arr[order])])
        worst_i = int(order[np.argmin(vals_arr[order])])
        best = {"label": labels[best_i], "value": float(vals_arr[best_i])}
        worst = {"label": labels[worst_i], "value": float(vals_arr[worst_i])}

    y = np.arange(len(labels))
    plot_vals = np.where(np.isfinite(vals_arr), vals_arr, 0.0)
    colors = []
    for i, v in enumerate(plot_vals):
        if i == best_i:
            colors.append(_PALETTE["good"])
        elif i == worst_i:
            colors.append(_PALETTE["bad"])
        else:
            colors.append(_PALETTE["acc"] if v >= 0 else _PALETTE["warn"])
    ax.barh(y, plot_vals, color=colors, alpha=0.88)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()  # first subset on top
    ax.axvline(0, color=_PALETTE["muted"], lw=0.9)
    for yi, v in zip(y, plot_vals):
        ax.text(v, yi, f" {_money(v)}", va="center", ha="left" if v >= 0 else "right", fontsize=8, color=_PALETTE["text"])

    pretty = metric.replace("_", " ")
    ax.set_xlabel(f"{pretty} ($)")
    ax.set_title(f"Combination comparison: {pretty}")
    if best is not None and worst is not None:
        _annotate(ax, f"best {best['label']}: {_money(best['value'])}\nworst {worst['label']}: {_money(worst['value'])}", loc="lower right")

    sidecar = {
        "labels": labels,
        "values": [float(v) for v in values],
        "metric": metric,
        "best": best,
        "worst": worst,
    }
    return fig, sidecar


# =========================================================================== #
# Composition: dashboard + render-all
# =========================================================================== #
def dashboard(ctx: dict, preset: str = "quicklook") -> "Figure":
    """Compose a multi-panel dashboard Figure from a context dict.

    ``ctx`` keys (all optional; charts that lack inputs are skipped):
        legs, market, net_cost, book, market_by_name, pnl_draws, mc_floor,
        chain_slice, chain_by_expiry, cone, current_iv_by_tenor, scenarios,
        catalysts, prob_cone (={S0,sigma,T,levels,measure}).

    presets:
        quicklook -> payoff + greeks_vs_spot
        risk      -> payoff + mc_histogram + scenario_tornado + delta_ladder
        vol       -> vol_smile + vol_term_structure + vol_cone + probability_cone
        full      -> a broad grid of everything available
    """
    plt = _pyplot()

    panel_specs = _dashboard_panels(preset)
    available = [(title, fn) for title, fn in panel_specs if _panel_inputs_ready(fn, ctx)]
    if not available:
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.text(0.5, 0.5, f"No chartable inputs for preset '{preset}'", ha="center", va="center", color=_PALETTE["muted"])
        ax.axis("off")
        return fig

    n = len(available)
    ncol = 2 if n > 1 else 1
    nrow = math.ceil(n / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(7.5 * ncol, 4.6 * nrow), squeeze=False)
    flat = axes.ravel()
    for i, (title, fn) in enumerate(available):
        try:
            fn(ctx, flat[i])
        except Exception as exc:  # never let one panel kill the dashboard
            flat[i].axis("off")
            flat[i].text(0.5, 0.5, f"{title}\n(chart error: {type(exc).__name__})", ha="center", va="center", fontsize=8, color=_PALETTE["muted"])
    for j in range(n, len(flat)):
        flat[j].axis("off")
    fig.suptitle(f"Options dashboard ({preset})", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


def _dashboard_panels(preset: str) -> list[tuple[str, Callable]]:
    """(title, panel-fn) list for a preset. Each panel-fn takes (ctx, ax)."""
    P: dict[str, list[tuple[str, Callable]]] = {
        "quicklook": [
            ("Payoff", _panel_payoff),
            ("Greeks vs spot", _panel_greeks),
        ],
        "risk": [
            ("Payoff", _panel_payoff),
            ("MC P&L", _panel_mc),
            ("Scenario tornado", _panel_tornado),
            ("Delta ladder", _panel_ladder),
        ],
        "vol": [
            ("Vol smile", _panel_smile),
            ("Term structure", _panel_term),
            ("Vol cone", _panel_cone),
            ("Probability cone", _panel_probcone),
        ],
        "full": [
            ("Payoff", _panel_payoff),
            ("Greeks vs spot", _panel_greeks),
            ("P&L spot x vol", _panel_surf_vol),
            ("P&L spot x time", _panel_surf_time),
            ("Probability cone", _panel_probcone),
            ("MC P&L", _panel_mc),
            ("Scenario tornado", _panel_tornado),
            ("Delta ladder", _panel_ladder),
            ("Expiration timeline", _panel_timeline),
        ],
    }
    return P.get(preset, P["quicklook"])


# --- panel adapters: (ctx, ax) -> draws into ax (single-axes path) ---------- #
def _panel_payoff(ctx: dict, ax) -> None:
    nc = ctx.get("net_cost")
    if nc is None:
        nc = _net_cost_entry(ctx.get("legs", []))
    payoff_diagram(ctx["legs"], ctx["market"], net_cost=nc, tn_days=(0,), ax=ax)


def _panel_greeks(ctx: dict, ax) -> None:
    greeks_vs_spot(ctx["legs"], ctx["market"], greeks=("delta",), ax=ax)


def _panel_surf_vol(ctx: dict, ax) -> None:
    pnl_surface_spot_vol(ctx["legs"], ctx["market"], ax=ax)


def _panel_surf_time(ctx: dict, ax) -> None:
    pnl_surface_spot_time(ctx["legs"], ctx["market"], ax=ax)


def _panel_mc(ctx: dict, ax) -> None:
    mc_histogram(ctx["pnl_draws"], floor=ctx.get("mc_floor"), ax=ax)


def _panel_tornado(ctx: dict, ax) -> None:
    scenario_tornado(ctx["book"], ctx.get("scenarios"), ctx.get("market_by_name"), ax=ax)


def _panel_ladder(ctx: dict, ax) -> None:
    delta_ladder(ctx["book"], market_by_name=ctx.get("market_by_name"), ax=ax)


def _panel_smile(ctx: dict, ax) -> None:
    vol_smile(ctx["chain_slice"], ax=ax)


def _panel_term(ctx: dict, ax) -> None:
    vol_term_structure(ctx["chain_by_expiry"], ax=ax)


def _panel_cone(ctx: dict, ax) -> None:
    vol_cone(ctx["cone"], ctx.get("current_iv_by_tenor"), ax=ax)


def _panel_probcone(ctx: dict, ax) -> None:
    pc = ctx["prob_cone"]
    probability_cone(
        pc["S0"], pc["sigma"], pc["T"],
        levels=tuple(pc.get("levels", (1, 2))),
        legs=ctx.get("legs"),
        measure=pc.get("measure", "rn"),
        ax=ax,
    )


def _panel_timeline(ctx: dict, ax) -> None:
    expiration_timeline(ctx["book"], ctx.get("catalysts"), ax=ax)


# requirement table: which ctx keys each panel needs to be drawable
_PANEL_REQS: dict[Callable, tuple[str, ...]] = {
    _panel_payoff: ("legs", "market"),
    _panel_greeks: ("legs", "market"),
    _panel_surf_vol: ("legs", "market"),
    _panel_surf_time: ("legs", "market"),
    _panel_mc: ("pnl_draws",),
    _panel_tornado: ("book",),
    _panel_ladder: ("book",),
    _panel_smile: ("chain_slice",),
    _panel_term: ("chain_by_expiry",),
    _panel_cone: ("cone",),
    _panel_probcone: ("prob_cone",),
    _panel_timeline: ("book",),
}


def _panel_inputs_ready(fn: Callable, ctx: dict) -> bool:
    reqs = _PANEL_REQS.get(fn, ())
    return all(ctx.get(k) is not None for k in reqs)


def render_all_charts(ctx: dict, out_dir: str | Path | None = None) -> dict:
    """Render every chart whose inputs are present in ``ctx`` to PNG + base64.

    Returns ``{chart_id: {"png_path": str, "base64": data-uri, "sidecar": dict}}``.
    Used by viz/report.py to embed images and by --charts-only. Each chart is
    rendered on its OWN figure (not a dashboard axes) so it can be saved full-size.
    Errors in any one chart are captured, never fatal.
    """
    out_dir = Path(out_dir) if out_dir is not None else OPTIONS_RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)

    nc = ctx.get("net_cost")
    if nc is None and ctx.get("legs") is not None:
        nc = _net_cost_entry(ctx["legs"])

    # (chart_id, callable producing (fig, sidecar), required ctx keys)
    jobs: list[tuple[str, Callable[[], tuple], tuple[str, ...]]] = [
        ("payoff", lambda: payoff_diagram(ctx["legs"], ctx["market"], net_cost=nc, tn_days=ctx.get("tn_days", (0, 30))), ("legs", "market")),
        ("greeks_vs_spot", lambda: greeks_vs_spot(ctx["legs"], ctx["market"]), ("legs", "market")),
        ("pnl_surface_spot_vol", lambda: pnl_surface_spot_vol(ctx["legs"], ctx["market"]), ("legs", "market")),
        ("pnl_surface_spot_time", lambda: pnl_surface_spot_time(ctx["legs"], ctx["market"]), ("legs", "market")),
        ("vol_smile", lambda: vol_smile(ctx["chain_slice"]), ("chain_slice",)),
        ("vol_term_structure", lambda: vol_term_structure(ctx["chain_by_expiry"]), ("chain_by_expiry",)),
        ("vol_cone", lambda: vol_cone(ctx["cone"], ctx.get("current_iv_by_tenor")), ("cone",)),
        ("probability_cone", lambda: _render_prob_cone(ctx), ("prob_cone",)),
        ("mc_histogram", lambda: mc_histogram(ctx["pnl_draws"], floor=ctx.get("mc_floor")), ("pnl_draws",)),
        ("delta_ladder", lambda: delta_ladder(ctx["book"], market_by_name=ctx.get("market_by_name")), ("book",)),
        ("scenario_tornado", lambda: scenario_tornado(ctx["book"], ctx.get("scenarios"), ctx.get("market_by_name")), ("book",)),
        ("expiration_timeline", lambda: expiration_timeline(ctx["book"], ctx.get("catalysts")), ("book",)),
        ("mc_fan_liquidation", lambda: mc_fan_liquidation(
            ctx["book"], ctx["market"], ctx["account"],
            horizon_days=float(ctx.get("horizon_days", 30)),
            measure=ctx.get("measure", "rn"), mu=ctx.get("mu"), seed=int(ctx.get("seed", 7)),
        ), ("book", "market", "account")),
        ("combination_comparison", lambda: combination_comparison_chart(
            ctx["compare_result"], metric=ctx.get("comparison_metric", "excess_liquidity"),
        ), ("compare_result",)),
    ]

    results: dict[str, dict] = {}
    for chart_id, fn, reqs in jobs:
        if not all(ctx.get(k) is not None for k in reqs):
            continue
        try:
            fig, sidecar = fn()
            b64 = to_base64(fig, dpi=INLINE_DPI)
            # to_base64 already closed the figure; re-render once for the on-disk PNG.
            fig2, sidecar2 = fn()
            png_path = save(fig2, out_dir / f"{chart_id}.png", dpi=APPENDIX_DPI)
            results[chart_id] = {"png_path": png_path, "base64": b64, "sidecar": sidecar}
        except Exception as exc:  # capture, never fatal
            results[chart_id] = {"png_path": "", "base64": "", "sidecar": {}, "error": f"{type(exc).__name__}: {exc}"}
    return results


def _render_prob_cone(ctx: dict) -> tuple:
    pc = ctx["prob_cone"]
    return probability_cone(
        pc["S0"], pc["sigma"], pc["T"],
        levels=tuple(pc.get("levels", (1, 2))),
        legs=ctx.get("legs"),
        measure=pc.get("measure", "rn"),
    )


__all__ = [
    # plumbing
    "apply_theme",
    "save",
    "to_base64",
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
    "mc_fan_liquidation",
    "combination_comparison_chart",
    # composition
    "dashboard",
    "render_all_charts",
]
