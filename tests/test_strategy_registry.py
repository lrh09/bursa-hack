"""Tests for the strategy registry: id generation + variant grouping."""
from __future__ import annotations

import pandas as pd
import pytest

from bursahack.signals.base import Strategy
from bursahack.signals.registry import (
    all_strategies,
    group_variants_by_strategy,
    strategy_id_for,
)


class _RegimeStrat(Strategy):
    DISPLAY_NAME = "Regime test"
    SHAPE_KEYS = ("use_regime", "rebal_freq")
    CONT_KEYS = ("lookback",)
    SHORT_BLURB = "x"
    DEFINITION_MD = "x"
    SOURCE_FILE = "x"
    ADDED = "2026-05-18"

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


class _NoShape(Strategy):
    DISPLAY_NAME = "No shape"
    SHAPE_KEYS = ()
    SHORT_BLURB = "x"
    DEFINITION_MD = "x"
    SOURCE_FILE = "x"
    ADDED = "2026-05-18"

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


def test_strategy_id_no_shape_keys_returns_family_only():
    sid = strategy_id_for("donchian_breakout", _NoShape.SHAPE_KEYS, {"lookback": 60})
    assert sid == "donchian_breakout"


def test_strategy_id_bool_normalised():
    sid = strategy_id_for(
        "clenow_som",
        ("use_regime", "rebal_freq"),
        {"use_regime": True, "rebal_freq": "M", "lookback": 60},
    )
    assert sid == "clenow_som__regime-on__rebal-M"


def test_strategy_id_bool_false_normalised():
    sid = strategy_id_for(
        "clenow_som",
        ("use_regime", "rebal_freq"),
        {"use_regime": False, "rebal_freq": "W"},
    )
    assert sid == "clenow_som__regime-off__rebal-W"


def test_strategy_id_declaration_order_preserved():
    sid_a = strategy_id_for("x", ("a", "b"), {"b": "B", "a": "A"})
    sid_b = strategy_id_for("x", ("a", "b"), {"a": "A", "b": "B"})
    assert sid_a == sid_b == "x__a-A__b-B"


def test_strategy_id_string_freq_lowercased_with_prefix():
    sid = strategy_id_for("rotation", ("rebal_freq",), {"rebal_freq": "Q"})
    assert sid == "rotation__rebal-Q"


def test_group_variants_by_strategy_collapses_continuous_only_variants():
    rows = [
        {"strategy": "clenow_som", "params_hash": "h1",
         "params": {"use_regime": True, "rebal_freq": "M", "lookback": 60}},
        {"strategy": "clenow_som", "params_hash": "h2",
         "params": {"use_regime": True, "rebal_freq": "M", "lookback": 90}},
        {"strategy": "clenow_som", "params_hash": "h3",
         "params": {"use_regime": True, "rebal_freq": "M", "lookback": 120}},
    ]
    shape_keys_for = {"clenow_som": ("use_regime", "rebal_freq")}
    grouped = group_variants_by_strategy(rows, shape_keys_for)
    assert len(grouped) == 1
    sid, variants = next(iter(grouped.items()))
    assert sid == "clenow_som__regime-on__rebal-M"
    assert len(variants) == 3


def test_group_variants_by_strategy_splits_on_shape_difference():
    rows = [
        {"strategy": "clenow_som", "params_hash": "h1",
         "params": {"use_regime": True,  "rebal_freq": "M", "lookback": 60}},
        {"strategy": "clenow_som", "params_hash": "h2",
         "params": {"use_regime": False, "rebal_freq": "M", "lookback": 60}},
        {"strategy": "clenow_som", "params_hash": "h3",
         "params": {"use_regime": True,  "rebal_freq": "W", "lookback": 60}},
    ]
    shape_keys_for = {"clenow_som": ("use_regime", "rebal_freq")}
    grouped = group_variants_by_strategy(rows, shape_keys_for)
    assert set(grouped.keys()) == {
        "clenow_som__regime-on__rebal-M",
        "clenow_som__regime-off__rebal-M",
        "clenow_som__regime-on__rebal-W",
    }
    for variants in grouped.values():
        assert len(variants) == 1
