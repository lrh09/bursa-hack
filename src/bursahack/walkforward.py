"""Walk-forward fold generator + holdout guard.

Two-layer discipline:
  - Dev set:  2008-01-01 -> HOLDOUT_START (inclusive). Walk-forward inside this.
  - Holdout:  HOLDOUT_START -> end of data. Touched ONCE at the very end.

The harness exposes:
  - `dev_window` / `holdout_window` constants
  - `walk_forward_folds(...)` -> rolling (train, validate) windows for in-sample
    parameter selection
  - `assert_no_holdout_leak(...)` -- raise if a window touches the holdout

Embargo: between train.end and validate.start we insert a buffer = the strategy's
label horizon (default 21 trading days = 1 month). This prevents overlapping
return windows from leaking labels.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

HOLDOUT_START = pd.Timestamp("2020-01-01")
HOLDOUT_END = pd.Timestamp("2022-02-15")  # File end
DEV_START = pd.Timestamp("2008-01-01")   # First date with 252+21 history


@dataclass(frozen=True)
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validate_start: pd.Timestamp
    validate_end: pd.Timestamp


def assert_no_holdout_leak(end: pd.Timestamp) -> None:
    """Raise if a window's end touches or exceeds the holdout start."""
    if end >= HOLDOUT_START:
        raise AssertionError(
            f"WINDOW LEAKAGE: end={end.date()} >= HOLDOUT_START={HOLDOUT_START.date()}. "
            f"The holdout is sacred. Touched only by the final verdict run."
        )


def walk_forward_folds(
    train_years: int = 3,
    validate_years: int = 1,
    step_months: int = 6,
    embargo_days: int = 21,
    dev_start: pd.Timestamp = DEV_START,
    holdout_start: pd.Timestamp = HOLDOUT_START,
) -> list[Fold]:
    """Rolling train/validate folds inside the dev set.

    Returns a list of Fold objects. The last fold's `validate_end` will be
    strictly less than `holdout_start`.
    """
    folds = []
    train_start = dev_start
    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        # embargo
        validate_start = train_end + pd.Timedelta(days=embargo_days * 2)  # calendar-day buffer
        validate_end = validate_start + pd.DateOffset(years=validate_years)
        if validate_end >= holdout_start:
            break
        assert_no_holdout_leak(validate_end)
        folds.append(Fold(
            train_start=train_start,
            train_end=train_end,
            validate_start=validate_start,
            validate_end=validate_end,
        ))
        train_start = train_start + pd.DateOffset(months=step_months)
    return folds


def trim_panel(panel, start: pd.Timestamp, end: pd.Timestamp):
    """Return a copy of the panel restricted to [start, end]."""
    from bursahack.engine import PricePanel
    return PricePanel(
        adj_close=panel.adj_close.loc[start:end],
        adj_open=panel.adj_open.loc[start:end],
        raw_open=panel.raw_open.loc[start:end],
        volume_rm=panel.volume_rm.loc[start:end],
    )
