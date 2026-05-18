"""Strategy bank registry.

Reads the `Strategy.__subclasses__()` tree to discover declared strategies,
generates stable `strategy_id` slugs, and groups raw search-log variant rows
by `(family, shape_tuple)`.

The strategy_id format encodes shape-key values in *declaration order* (the
order they appear in `Strategy.SHAPE_KEYS`), so re-ordering a dict elsewhere
never changes a URL.

See: docs/superpowers/specs/2026-05-18-strategy-bank-design.md §3.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from bursahack.signals.base import Strategy


def all_strategies() -> list[type[Strategy]]:
    """Every concrete Strategy subclass currently imported.

    Note: only subclasses that have been *imported* show up. The single
    import-everything point is `bursahack.signals.__init__`. Callers should
    `import bursahack.signals` once before calling this.
    """
    seen: set[type[Strategy]] = set()
    out: list[type[Strategy]] = []
    stack: list[type[Strategy]] = list(Strategy.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        if cls.DISPLAY_NAME:
            out.append(cls)
        stack.extend(cls.__subclasses__())
    return out


def _slug_for_value(key: str, value: Any) -> str:
    """Normalise a shape-key value into the URL token.

    - booleans:  use_regime=True  -> "regime-on"
                 use_regime=False -> "regime-off"
                 generic bool key:  "{key}-on" / "{key}-off"
    - strings (freq codes):       "rebal-{value}"   (value kept as-is)
    - everything else:            "{key}-{value}"
    """
    if isinstance(value, bool):
        if key == "use_regime":
            return "regime-on" if value else "regime-off"
        return f"{key}-{'on' if value else 'off'}"
    if key == "rebal_freq":
        return f"rebal-{value}"
    return f"{key}-{value}"


def strategy_id_for(
    family: str,
    shape_keys: tuple[str, ...],
    params: dict[str, Any],
) -> str:
    """Generate the strategy_id slug for a (family, shape_tuple).

    No shape keys -> bare family. Otherwise:
        family__token1__token2... in SHAPE_KEYS declaration order.
    """
    if not shape_keys:
        return family
    parts = [family]
    for k in shape_keys:
        if k not in params:
            raise KeyError(f"shape key {k!r} missing from params for family {family!r}")
        parts.append(_slug_for_value(k, params[k]))
    return "__".join(parts)


def group_variants_by_strategy(
    rows: Iterable[dict],
    shape_keys_for: dict[str, tuple[str, ...]],
) -> dict[str, list[dict]]:
    """Bucket variant rows by (family, shape_tuple) into strategy_ids.

    `rows` is any iterable of dicts with keys at least `strategy` (family
    name) and `params` (the full param dict). `shape_keys_for` maps a
    family to its declared SHAPE_KEYS.

    Returns dict[strategy_id, list[row]]; order is insertion order
    (deterministic given deterministic row order).
    """
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        family = row["strategy"]
        shape_keys = shape_keys_for.get(family, ())
        sid = strategy_id_for(family, shape_keys, row["params"])
        out[sid].append(row)
    return dict(out)
