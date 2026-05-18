"""HOLDOUT_LOCKED guard: refuse to run if any fold validate_end > 2019-12-31
unless the strategy class declares HOLDOUT_LOCKED=False AND --touch-holdout
is passed."""
from __future__ import annotations

import pandas as pd
import pytest

from bursahack.signals.base import Strategy
from bursahack.run_search import assert_holdout_safe


class _Locked(Strategy):
    DISPLAY_NAME = "L"; SHAPE_KEYS = (); SHORT_BLURB = "x"; DEFINITION_MD = "x"
    SOURCE_FILE = "x"; ADDED = "2026-05-18"
    # HOLDOUT_LOCKED defaults True

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


class _Unlocked(Strategy):
    DISPLAY_NAME = "U"; SHAPE_KEYS = (); SHORT_BLURB = "x"; DEFINITION_MD = "x"
    SOURCE_FILE = "x"; ADDED = "2026-05-18"
    HOLDOUT_LOCKED = False

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


def _fold(validate_end):
    return {"validate_end": pd.Timestamp(validate_end)}


def test_in_sample_folds_always_pass():
    folds = [_fold("2018-12-31"), _fold("2019-12-31")]
    assert_holdout_safe(_Locked, folds, touch_holdout=False)  # no raise


def test_oos_fold_with_locked_class_raises():
    folds = [_fold("2018-12-31"), _fold("2020-06-30")]
    with pytest.raises(RuntimeError, match=r"holdout"):
        assert_holdout_safe(_Locked, folds, touch_holdout=False)


def test_oos_fold_with_locked_class_and_cli_flag_still_raises():
    folds = [_fold("2020-06-30")]
    with pytest.raises(RuntimeError, match=r"HOLDOUT_LOCKED"):
        assert_holdout_safe(_Locked, folds, touch_holdout=True)


def test_oos_fold_with_unlocked_class_and_no_cli_flag_raises():
    folds = [_fold("2020-06-30")]
    with pytest.raises(RuntimeError, match=r"--touch-holdout"):
        assert_holdout_safe(_Unlocked, folds, touch_holdout=False)


def test_oos_fold_with_unlocked_class_and_cli_flag_passes():
    folds = [_fold("2020-06-30")]
    assert_holdout_safe(_Unlocked, folds, touch_holdout=True)  # no raise
