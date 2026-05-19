"""Combinatorial Purged Cross-Validation (Lopez de Prado, AFML 2018 ch.12).

Plain idea: instead of one walk-forward split, choose every k-fold subset of
folds as test simultaneously. With (n_folds=10, n_test_folds=2) you get
C(10,2)=45 splits per variant, each producing one test-period Sharpe. The
distribution of those 45 Sharpes is what the diagnostics layer consumes.

Purging + embargo (Lopez de Prado): the bars immediately adjacent to a test
fold leak label information into training because nearby observations share
overlapping windows / autocorrelation. We drop a configurable number of
observations on each side of each test fold from the training set.

Regime awareness is NOT enforced inside a split — folds are contiguous in
time. Regime balance is enforced post-hoc by reporting per-regime Sharpe in
the diagnostics layer (a fold dominated by one regime simply contributes a
regime-tilted Sharpe to the matrix). That keeps the splitter simple and
deterministic.

Surface:
  make_cpcv_splits(n, n_folds, n_test_folds, embargo) -> list[CPCVSplit]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from math import comb


@dataclass(frozen=True)
class CPCVSplit:
    """One combinatorial split: which indices are train, which are test."""

    split_id: int
    test_folds: tuple[int, ...]
    train_idx: list[int] = field(default_factory=list)
    test_idx: list[int] = field(default_factory=list)


def make_cpcv_splits(
    n: int,
    n_folds: int = 10,
    n_test_folds: int = 2,
    embargo: int = 0,
) -> list[CPCVSplit]:
    """Build every combinatorial-purged split over `n` ordered observations.

    Args:
      n: number of observations on the time axis (e.g., number of sessions).
      n_folds: total folds to slice the time axis into. Folds are
        contiguous, ordered, equal-sized (last fold absorbs the remainder).
      n_test_folds: how many folds are held out per split. Must be <
        n_folds and >= 1. The number of splits returned is C(n_folds,
        n_test_folds).
      embargo: number of observations to drop from training adjacent to
        each test fold's boundary. 0 disables. Lopez de Prado recommends
        ~1.5x the average holding-period length.

    Returns:
      List of CPCVSplit, length C(n_folds, n_test_folds). split_id is
      stable (lexicographic on test_folds).
    """
    if n_folds < 2:
        raise ValueError("n_folds must be >= 2")
    if not 1 <= n_test_folds < n_folds:
        raise ValueError("n_test_folds must be in [1, n_folds-1]")
    if n < n_folds:
        raise ValueError(f"n={n} < n_folds={n_folds}; cannot split")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")

    # Contiguous, equal-sized folds; the last one absorbs the remainder
    # so that every observation lands in exactly one fold.
    fold_size = n // n_folds
    bounds: list[tuple[int, int]] = []
    for f in range(n_folds - 1):
        bounds.append((f * fold_size, (f + 1) * fold_size))
    bounds.append(((n_folds - 1) * fold_size, n))

    splits: list[CPCVSplit] = []
    for sid, combo in enumerate(combinations(range(n_folds), n_test_folds)):
        test_set: set[int] = set()
        embargo_set: set[int] = set()
        for f in combo:
            lo, hi = bounds[f]
            for j in range(lo, hi):
                test_set.add(j)
            if embargo > 0:
                # left side: [lo-embargo, lo)
                for j in range(max(0, lo - embargo), lo):
                    embargo_set.add(j)
                # right side: [hi, hi+embargo)
                for j in range(hi, min(n, hi + embargo)):
                    embargo_set.add(j)
        # train = everything not in test and not in embargo
        train_idx = [
            i for i in range(n)
            if i not in test_set and i not in embargo_set
        ]
        test_idx = sorted(test_set)
        splits.append(CPCVSplit(
            split_id=sid,
            test_folds=combo,
            train_idx=train_idx,
            test_idx=test_idx,
        ))

    expected = comb(n_folds, n_test_folds)
    assert len(splits) == expected, f"got {len(splits)} splits, expected {expected}"
    return splits


def n_oos_paths(n_folds: int, n_test_folds: int) -> int:
    """Number of non-overlapping OOS paths reconstructable from the splits.

    By the AFML CPCV identity, each fold appears in C(n_folds-1,
    n_test_folds-1) splits. The number of independent paths you can
    stitch from the test segments is C(n_folds-1, n_test_folds-1).
    For (10, 2) that's 9. For (8, 2) that's 7.
    """
    return comb(n_folds - 1, n_test_folds - 1)


__all__ = ["CPCVSplit", "make_cpcv_splits", "n_oos_paths"]
