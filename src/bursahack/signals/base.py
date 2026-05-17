"""Strategy interface.

A strategy plugs into the engine by producing target weights at each rebal date.
Subclasses implement two methods:

    eligibility(t, panel) -> set[SECURITY_ID]
        Which securities are tradeable on date t. Engine filters scores against
        this set before sizing.

    score(t, panel, eligible) -> pd.Series
        A scalar per eligible security; engine top-N's by score and weights.

The default `weights()` method takes the score and picks the top-N by score
with equal weighting. Override if you want vol-scaling / custom weighting.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from bursahack.engine import PricePanel
from bursahack.portfolio import top_n_equal_weight


@dataclass
class Strategy(ABC):
    name: str
    params: dict[str, Any]

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
