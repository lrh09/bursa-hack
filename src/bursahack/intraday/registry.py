"""Decorator-based strategy registry for intraday families.

Design contract:
  - Strategies declare themselves via the `@register_strategy(name=...)` decorator.
  - Each strategy class exposes (a) a pydantic `params_model`, (b) a semantic
    `version` string -- bumping the version invalidates result-cache entries,
    and (c) a `generate_signals` method returning a `SignalFrame`.
  - A `SignalFrame` row is the canonical point-in-time signal: timestamp `ts`,
    code, side (-1/0/+1), and optional entry/stop/target/exit_at hints.
  - The engine -- NOT the strategy -- is responsible for fills, P&L, and
    risk overlays. The strategy only declares "what would I do at ts".

Lookahead invariant (engine-enforced; documented here):
  - Signals are POINT-IN-TIME. A signal with `ts=t` may not depend on any
    bar with timestamp > t. The engine asserts this implicitly via
    next-bar-open fills (see `engine.IntradayEngine`).

This module has zero data dependencies and lives at the bottom of the import
graph -- strategies pull from here, the engine pulls from here, nothing
pulls back.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable

import polars as pl
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# SignalFrame schema
# ---------------------------------------------------------------------------


SIGNAL_SCHEMA: dict[str, pl.DataType] = {
    "ts": pl.Datetime("ns"),
    "code": pl.Utf8,
    "side": pl.Int8,            # -1 short, 0 flat, +1 long
    "entry_price": pl.Float64,  # optional hint; engine may override per fill_policy
    "stop_price": pl.Float64,
    "target_price": pl.Float64,
    "exit_at": pl.Datetime("ns"),  # optional time-based exit
}


def empty_signal_frame() -> pl.DataFrame:
    """Return a zero-row DataFrame with the canonical SignalFrame schema."""
    return pl.DataFrame(schema=SIGNAL_SCHEMA)


def validate_signal_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Ensure `df` matches the SignalFrame schema.

    Adds any missing optional columns as null-typed columns and re-orders to
    canonical column order. Raises ValueError on hard violations (bad side
    values, missing ts/code, wrong ts dtype family).
    """
    cols = set(df.columns)
    required = {"ts", "code", "side"}
    missing = required - cols
    if missing:
        raise ValueError(f"SignalFrame missing required columns: {sorted(missing)}")

    schema = df.schema
    if schema["ts"].base_type() != pl.Datetime:
        raise ValueError(f"SignalFrame.ts must be Datetime, got {schema['ts']}")

    # Add optional columns as nulls if absent
    add_cols: list[pl.Expr] = []
    for col, dtype in SIGNAL_SCHEMA.items():
        if col not in cols:
            add_cols.append(pl.lit(None, dtype=dtype).alias(col))
    if add_cols:
        df = df.with_columns(add_cols)

    # Validate side ∈ {-1, 0, 1}
    bad = df.filter(~pl.col("side").is_in([-1, 0, 1])).height
    if bad:
        raise ValueError(f"SignalFrame.side must be in {{-1, 0, 1}}; {bad} rows violate")

    return df.select(list(SIGNAL_SCHEMA.keys()))


# ---------------------------------------------------------------------------
# Strategy protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Strategy(Protocol):
    """Structural type for a registered intraday strategy.

    Implementations are classes (not instances) registered via
    `@register_strategy`. The engine instantiates the strategy as needed (or
    just calls the classmethod -- typical implementations are stateless).
    """

    name: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]
    version: ClassVar[str]

    def generate_signals(
        self,
        bars: pl.LazyFrame,
        universe_members: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:  # SignalFrame
        ...


# ---------------------------------------------------------------------------
# Spec + registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategySpec:
    """Registry entry describing a strategy family.

    Members:
      cls           -- the strategy class itself.
      name          -- family key (e.g. "orb").
      version       -- semantic version. BUMPING THIS INVALIDATES CACHE.
      params_model  -- pydantic model for the strategy's parameters.
      description   -- one-line human-readable note.
    """

    cls: type
    name: str
    version: str
    params_model: type[BaseModel]
    description: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StrategySpec):
            return NotImplemented
        return (
            self.name == other.name
            and self.version == other.version
            and self.params_model is other.params_model
            and self.cls is other.cls
        )

    def __hash__(self) -> int:
        return hash((self.name, self.version, id(self.cls)))


STRATEGY_REGISTRY: dict[str, StrategySpec] = {}


def register_strategy(name: str, *, description: str = ""):
    """Class decorator: register `cls` in `STRATEGY_REGISTRY` under `name`.

    Requires the decorated class to expose `params_model` and `version`
    ClassVars. The decorator also sets `cls.name = name` so users don't need
    to spell it twice.
    """

    def _wrap(cls: type):
        if not hasattr(cls, "params_model"):
            raise TypeError(f"strategy {cls.__name__!r} missing `params_model`")
        if not hasattr(cls, "version"):
            raise TypeError(f"strategy {cls.__name__!r} missing `version`")
        cls.name = name
        spec = StrategySpec(
            cls=cls,
            name=name,
            version=getattr(cls, "version"),
            params_model=getattr(cls, "params_model"),
            description=description,
        )
        STRATEGY_REGISTRY[name] = spec
        return cls

    return _wrap


def get_strategy(name: str) -> StrategySpec:
    """Lookup a registered strategy. Raises KeyError if unknown."""
    if name not in STRATEGY_REGISTRY:
        raise KeyError(
            f"unknown strategy {name!r}; registered: {sorted(STRATEGY_REGISTRY)}"
        )
    return STRATEGY_REGISTRY[name]


def list_strategies() -> list[str]:
    """Return the sorted list of registered strategy names."""
    return sorted(STRATEGY_REGISTRY.keys())


__all__ = [
    "SIGNAL_SCHEMA",
    "STRATEGY_REGISTRY",
    "Strategy",
    "StrategySpec",
    "empty_signal_frame",
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "validate_signal_frame",
]
