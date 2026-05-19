"""Tests for the CPCV splitter."""
from __future__ import annotations

from math import comb

import pytest

from bursahack.intraday.cpcv import (
    CPCVSplit,
    make_cpcv_splits,
    n_oos_paths,
)


def test_count_matches_combinations_identity():
    splits = make_cpcv_splits(n=1000, n_folds=10, n_test_folds=2)
    assert len(splits) == comb(10, 2) == 45


def test_train_test_are_disjoint():
    splits = make_cpcv_splits(n=200, n_folds=8, n_test_folds=2, embargo=0)
    for s in splits:
        assert set(s.train_idx).isdisjoint(s.test_idx)


def test_every_observation_is_covered_when_no_embargo():
    n = 400
    splits = make_cpcv_splits(n=n, n_folds=10, n_test_folds=2, embargo=0)
    for s in splits:
        assert set(s.train_idx) | set(s.test_idx) == set(range(n))


def test_embargo_removes_neighbours():
    n = 100
    n_folds = 5
    n_test = 1
    embargo = 3
    splits = make_cpcv_splits(n=n, n_folds=n_folds, n_test_folds=n_test, embargo=embargo)
    # Take the split where fold 2 (indices [40,60)) is held out.
    target = next(s for s in splits if s.test_folds == (2,))
    # Embargo zones: [37,40) on the left, [60,63) on the right.
    for j in (37, 38, 39, 60, 61, 62):
        assert j not in target.train_idx, f"embargo failed: {j} leaked into train"
    # Inside test fold itself, of course missing from train.
    for j in range(40, 60):
        assert j not in target.train_idx


def test_each_fold_appears_in_predicted_split_count():
    # Each fold k appears in C(n_folds-1, n_test_folds-1) splits.
    n_folds = 10
    n_test = 2
    splits = make_cpcv_splits(n=1000, n_folds=n_folds, n_test_folds=n_test)
    counts = {k: 0 for k in range(n_folds)}
    for s in splits:
        for f in s.test_folds:
            counts[f] += 1
    expected = comb(n_folds - 1, n_test - 1)  # = 9
    assert all(c == expected for c in counts.values()), counts


def test_n_oos_paths_identity():
    assert n_oos_paths(10, 2) == 9
    assert n_oos_paths(8, 2) == 7
    assert n_oos_paths(6, 3) == 10


def test_rejects_bad_args():
    with pytest.raises(ValueError):
        make_cpcv_splits(n=100, n_folds=1, n_test_folds=1)
    with pytest.raises(ValueError):
        make_cpcv_splits(n=100, n_folds=5, n_test_folds=0)
    with pytest.raises(ValueError):
        make_cpcv_splits(n=100, n_folds=5, n_test_folds=5)
    with pytest.raises(ValueError):
        make_cpcv_splits(n=3, n_folds=5, n_test_folds=2)
    with pytest.raises(ValueError):
        make_cpcv_splits(n=100, n_folds=5, n_test_folds=2, embargo=-1)


def test_split_ids_are_stable_and_unique():
    splits = make_cpcv_splits(n=500, n_folds=8, n_test_folds=2)
    ids = [s.split_id for s in splits]
    assert ids == sorted(ids) == list(range(len(splits)))
