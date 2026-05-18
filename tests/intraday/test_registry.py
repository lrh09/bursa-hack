"""Tests for the decorator-based strategy registry."""
from __future__ import annotations

from typing import ClassVar

import polars as pl
import pytest
from pydantic import BaseModel

from bursahack.intraday.registry import (
    STRATEGY_REGISTRY,
    StrategySpec,
    get_strategy,
    list_strategies,
    register_strategy,
    validate_signal_frame,
)


class _DummyParams(BaseModel):
    n: int = 5


def test_register_and_retrieve():
    """Registering a strategy makes it visible to get_strategy + list_strategies."""

    @register_strategy("dummy_v1", description="test")
    class Dummy:
        params_model: ClassVar[type[BaseModel]] = _DummyParams
        version: ClassVar[str] = "0.1.0"

        def generate_signals(self, bars, members, params):
            return pl.DataFrame()

    assert "dummy_v1" in STRATEGY_REGISTRY
    spec = get_strategy("dummy_v1")
    assert spec.name == "dummy_v1"
    assert spec.version == "0.1.0"
    assert spec.params_model is _DummyParams
    assert spec.cls is Dummy
    assert "dummy_v1" in list_strategies()

    # cleanup
    STRATEGY_REGISTRY.pop("dummy_v1", None)


def test_version_bump_invalidates_equality():
    """StrategySpec equality flips when version is bumped."""

    @register_strategy("vb_a")
    class A:
        params_model: ClassVar[type[BaseModel]] = _DummyParams
        version: ClassVar[str] = "1.0.0"

        def generate_signals(self, bars, members, params): ...

    spec_a = get_strategy("vb_a")

    @register_strategy("vb_a")
    class B:
        params_model: ClassVar[type[BaseModel]] = _DummyParams
        version: ClassVar[str] = "2.0.0"

        def generate_signals(self, bars, members, params): ...

    spec_b = get_strategy("vb_a")
    assert spec_a != spec_b
    assert spec_b.version == "2.0.0"

    STRATEGY_REGISTRY.pop("vb_a", None)


def test_missing_attrs_raise():
    with pytest.raises(TypeError, match="params_model"):
        @register_strategy("broken1")
        class Broken1:
            version = "1.0.0"
    with pytest.raises(TypeError, match="version"):
        @register_strategy("broken2")
        class Broken2:
            params_model = _DummyParams


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        get_strategy("nope-not-registered")


def test_validate_signal_frame_pads_optional_columns():
    """validate_signal_frame fills missing optional cols with nulls."""
    df = pl.DataFrame({
        "ts": [None, None],
        "code": ["A", "B"],
        "side": [1, 0],
    }, schema={"ts": pl.Datetime("ns"), "code": pl.Utf8, "side": pl.Int8})
    out = validate_signal_frame(df)
    for col in ("entry_price", "stop_price", "target_price", "exit_at"):
        assert col in out.columns


def test_validate_signal_frame_rejects_bad_side():
    df = pl.DataFrame({
        "ts": [None],
        "code": ["A"],
        "side": [2],  # invalid
    }, schema={"ts": pl.Datetime("ns"), "code": pl.Utf8, "side": pl.Int8})
    with pytest.raises(ValueError, match="side"):
        validate_signal_frame(df)
