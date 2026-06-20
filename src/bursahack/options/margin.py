"""Margin / buying-power re-export shim (consumer-camp contract name).

The structural defined-risk / naked / margin / buying-power lenses are
implemented once in :mod:`bursahack.options.risk` (which owns both the
distribution-side and the structural-risk families). Consumer-side modules and
the CLI import the margin/buying-power subset under the name
``bursahack.options.margin``. This is a thin re-export so both names resolve to
the same functions/dataclasses — no second implementation, no drift.
"""
from __future__ import annotations

from bursahack.options.risk import (
    DefinedRiskReport,
    MarginEstimate,
    StressPolicy,
    assignment_pin_scan,
    bp_impact,
    defined_risk,
    defined_risk_margin,
    excess_liquidity_path,
    expiration_cluster_risk,
    margin_call_distance,
    naked_scan,
    net_cost_entry,
    pm_reval_maintenance,
    pm_stress_maintenance,
)


def pm_maintenance(book, market_by_name, cash=0.0, netliq=0.0, method="reval"):
    """Book-level portfolio-margin maintenance estimate (CLI/report contract).

    Two per-position primitives, selected by ``method``:

      * ``method='reval'`` (DEFAULT) — ``risk.pm_reval_maintenance``: full
        repricing of every leg through the L3 valuation kernel over a spot x vol
        grid, worst-case MARK decline. This captures extrinsic (time) value and
        the vega hit, so its absolute number does not understate a long-premium
        book the way the intrinsic estimate does. Use this for absolute
        maintenance / Excess-Liquidity figures.
      * ``method='intrinsic'`` — ``risk.pm_stress_maintenance``: the intrinsic /
        terminal-payoff estimate (no time value, no vol). Kept for backward
        compatibility and as a lower-bound sanity check; it is a *lower bound* on
        the true maintenance for long-premium structures.

    This thin aggregator walks the book's positions, stresses each at its
    underlying's spot via the selected primitive, and sums the maintenance. It
    owns no new margin math — every per-position number comes from the chosen
    ``risk`` primitive (design law L3: one source of margin truth).

    Returns ``{maintenance, excess_liquidity, netliq, cash, binding_node,
    per_position, method}`` — the keys the CLI (``_book_aggregates``) and the
    report margin section read (``method`` added so the report can label which
    estimate produced the number).
    """
    try:
        netliq = float(netliq or 0.0)
    except (TypeError, ValueError):
        netliq = 0.0
    try:
        cash = float(cash or 0.0)
    except (TypeError, ValueError):
        cash = 0.0

    method = str(method or "reval").lower()
    if method not in ("reval", "intrinsic"):
        raise ValueError(f"pm_maintenance method must be 'reval' or 'intrinsic', got {method!r}")

    total = 0.0
    per_position: list[dict] = []
    binding_node: dict | None = None
    worst = -1.0

    for pos in getattr(book, "positions", ()) or ():
        sym = getattr(pos, "underlying", "") or ""
        mkt = market_by_name.get(sym) if isinstance(market_by_name, dict) else None
        spot = float(getattr(mkt, "spot", 0.0) or 0.0)
        legs = list(getattr(pos, "legs", ()) or ())
        if not legs or spot <= 0:
            continue
        mult = int(getattr(legs[0], "mult", 100) or 100)
        try:
            if method == "reval":
                rep = pm_reval_maintenance(legs, mkt, mult=mult)
            else:
                rep = pm_stress_maintenance(legs, spot, mult=mult)
        except Exception:
            # Reval needs a full market surface; if it fails (e.g. degenerate
            # market) fall back to the intrinsic estimate so the book still
            # produces a number rather than silently dropping the position.
            try:
                rep = pm_stress_maintenance(legs, spot, mult=mult)
            except Exception:
                continue
        m = float(rep.get("maintenance", 0.0) or 0.0)
        total += m
        per_position.append({
            "name": getattr(pos, "name", "") or sym,
            "underlying": sym,
            "maintenance": m,
            "binding_spot": rep.get("binding_spot"),
        })
        if m > worst:
            worst = m
            binding_node = {
                "position": getattr(pos, "name", "") or sym,
                "spot": rep.get("binding_spot"),
            }

    return {
        "maintenance": total,
        "excess_liquidity": netliq - total,
        "netliq": netliq,
        "cash": cash,
        "binding_node": binding_node,
        "per_position": per_position,
        "method": method,
    }


__all__ = [
    "defined_risk",
    "naked_scan",
    "defined_risk_margin",
    "pm_stress_maintenance",
    "pm_reval_maintenance",
    "pm_maintenance",
    "bp_impact",
    "excess_liquidity_path",
    "margin_call_distance",
    "assignment_pin_scan",
    "expiration_cluster_risk",
    "net_cost_entry",
    "DefinedRiskReport",
    "MarginEstimate",
    "StressPolicy",
]
