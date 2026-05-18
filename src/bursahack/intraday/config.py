"""Pydantic + YAML layered config.

Scalability note: configs use a top-level `_extends:` key for inheritance.
A variant config inherits from a family config which inherits from defaults.
Conflicting keys: child wins (deep merge). This is the same shape Hydra
uses but ~30 lines of code instead of a framework dependency.

Example:
    # configs/intraday/families/orb.yaml
    _extends: ../defaults.yaml
    universe:
      top_n: 50
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bursahack.costs import FeeSchedule, MPlusRetailFee
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.universe import Universe


# ----------------------------- sub-sections -----------------------------


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_root: str = "data/intraday"
    snapshot_start: date = Field(...)
    snapshot_end: date = Field(...)
    exchange: str = "XKLS"


class EngineConfig(BaseModel):
    """W1.B placeholder. Defaults match the proposal's iron rules."""
    model_config = ConfigDict(extra="forbid")
    fill_policy: str = "next_bar_open"
    session_only: bool = True


class IntradayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "default"
    data: DataConfig
    universe: Universe
    impact: ImpactModel = Field(default_factory=ImpactModel)
    fees: list[FeeSchedule] = Field(default_factory=lambda: [MPlusRetailFee()])
    engine: EngineConfig = Field(default_factory=EngineConfig)


# ------------------------------ loader ------------------------------


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-merged dict; values in `b` override `a`."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_extends(path: Path, _seen: set[Path] | None = None) -> dict[str, Any]:
    """Recursively resolve `_extends:` chains, returning a fully-merged dict."""
    path = path.resolve()
    if _seen is None:
        _seen = set()
    if path in _seen:
        raise ValueError(f"circular _extends: chain at {path}")
    _seen = _seen | {path}

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    parent = raw.pop("_extends", None)
    if parent is None:
        return raw
    parent_path = (path.parent / parent).resolve()
    base = _resolve_extends(parent_path, _seen)
    return _deep_merge(base, raw)


def _resolve_fees(fees_raw: list[dict[str, Any]] | None) -> list[FeeSchedule]:
    """Map [{name: mplus_retail, ...}, ...] -> [MPlusRetailFee(...), ...]."""
    if not fees_raw:
        return [MPlusRetailFee()]
    from bursahack.costs import InstitutionalFee, CustomFee
    table: dict[str, type[FeeSchedule]] = {
        "mplus_retail": MPlusRetailFee,
        "institutional": InstitutionalFee,
        "custom": CustomFee,
    }
    out: list[FeeSchedule] = []
    for f in fees_raw:
        n = f.get("name")
        cls = table.get(n)
        if cls is None:
            raise ValueError(f"unknown fee name: {n!r}; expected one of {list(table)}")
        out.append(cls(**f))
    return out


def load_config(path: str | Path) -> IntradayConfig:
    """Load + validate an IntradayConfig from YAML at `path`, resolving _extends."""
    merged = _resolve_extends(Path(path))
    if "fees" in merged:
        merged["fees"] = _resolve_fees(merged["fees"])
    return IntradayConfig.model_validate(merged)


__all__ = [
    "DataConfig",
    "EngineConfig",
    "IntradayConfig",
    "load_config",
]
