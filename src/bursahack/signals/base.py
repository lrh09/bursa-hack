"""Strategy interface + bank metadata declaration.

A strategy plugs into the engine by producing target weights at each rebal date.
Subclasses implement two methods:

    eligibility(t, panel) -> set[SECURITY_ID]
        Which securities are tradeable on date t. Engine filters scores against
        this set before sizing.

    score(t, panel, eligible) -> pd.Series
        A scalar per eligible security; engine top-N's by score and weights.

The default `weights()` method takes the score and picks the top-N by score
with equal weighting. Override if you want vol-scaling / custom weighting.

Bank metadata ClassVars
-----------------------
Every Strategy subclass declares metadata that drives the portal's strategy
bank (`/strategies/...`). Mandatory fields are checked at build_data.py time.

SHAPE_KEYS classify params: a param is a shape key iff changing its value
would make a quant call the strategy "a different strategy" in conversation
(e.g. `use_regime`, `rebal_freq`). Continuous knobs (lookback, top_n) are
variants WITHIN a strategy, not new strategies. See:
docs/superpowers/specs/2026-05-18-strategy-bank-design.md §2.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from bursahack.engine import PricePanel
from bursahack.portfolio import top_n_equal_weight


@dataclass
class Strategy(ABC):
    name: str
    params: dict[str, Any]

    # --- Bank metadata (override on subclasses; defaults here) -------------
    DISPLAY_NAME:  ClassVar[str] = ""
    SHORT_BLURB:   ClassVar[str] = ""
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ()
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ()
    DEFINITION_MD: ClassVar[str] = ""
    REFERENCES:    ClassVar[tuple[dict, ...]] = ()
    SOURCE_FILE:   ClassVar[str] = ""
    ADDED:         ClassVar[str] = ""
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"
    HOLDOUT_LOCKED: ClassVar[bool] = True

    @abstractmethod
    def eligibility(self, t: pd.Timestamp, panel: PricePanel) -> set[str]:
        """Tradeable security IDs on date t."""

    @abstractmethod
    def score(self, t: pd.Timestamp, panel: PricePanel, eligible: set[str]) -> pd.Series:
        """Higher score = more attractive."""

    @abstractmethod
    def rebal_dates(self, panel: PricePanel) -> list[pd.Timestamp]:
        """Which dates the strategy fires on."""

    def weights(self, t: pd.Timestamp, panel: PricePanel) -> pd.Series:
        eligible = self.eligibility(t, panel)
        if not eligible:
            return pd.Series(dtype=float)
        scores = self.score(t, panel, eligible)
        if scores.empty:
            return pd.Series(dtype=float)
        scores = scores[scores.index.isin(eligible)]
        top_n = int(self.params.get("top_n", 20))
        ascending = bool(self.params.get("ascending", False))
        return top_n_equal_weight(scores, n=top_n, ascending=ascending)

    def signal_fn(self):
        """Return a callable matching engine.SignalFn — closes over self."""
        def _fn(t: pd.Timestamp, panel: PricePanel) -> pd.Series:
            return self.weights(t, panel)
        return _fn
