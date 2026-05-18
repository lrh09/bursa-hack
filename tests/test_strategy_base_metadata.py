"""Tests for Strategy base-class bank-metadata ClassVars."""
from __future__ import annotations

import pandas as pd
import pytest

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy


class _Dummy(Strategy):
    """Minimal subclass for testing defaults."""
    DISPLAY_NAME = "Dummy"
    SHAPE_KEYS = ()
    SHORT_BLURB = "test"
    DEFINITION_MD = "## Dummy\nfor tests"
    SOURCE_FILE = "tests/test_strategy_base_metadata.py"
    ADDED = "2026-05-18"

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


def test_holdout_locked_default_true():
    assert Strategy.HOLDOUT_LOCKED is True


def test_shape_keys_default_empty_tuple():
    assert Strategy.SHAPE_KEYS == ()


def test_cont_keys_default_empty_tuple():
    assert Strategy.CONT_KEYS == ()


def test_references_default_empty_tuple():
    assert Strategy.REFERENCES == ()


def test_headline_rule_default_max_wf_sharpe():
    assert Strategy.HEADLINE_RULE == "max wf_sharpe"


def test_dummy_inherits_defaults():
    assert _Dummy.HOLDOUT_LOCKED is True
    assert _Dummy.HEADLINE_RULE == "max wf_sharpe"
