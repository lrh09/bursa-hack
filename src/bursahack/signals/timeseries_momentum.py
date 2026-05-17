"""Time-Series Momentum (TSMOM).

Moskowitz, Ooi, Pedersen (2012). Unlike cross-sectional momentum which sorts
stocks against EACH OTHER, time-series momentum tests each stock against ITS OWN
past — only hold names whose own price is in an uptrend.

Combined signal here: include a name only if BOTH
  (a) it is above its own `trend_ma`-day moving average (own-trend filter), AND
  (b) its trailing `lookback`-day return is positive,
then rank surviving names by some downstream score. Default: equal-weight
across all qualifying names, capped at top_n.

This tends to reduce turnover and dropdown vs pure cross-sectional momentum
because the trend filter kicks names OUT during early downtrends, instead of
holding losers down to the bottom.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy


@dataclass
class TimeSeriesMomentum(Strategy):
    name: str = "tsmom"
    params: dict[str, Any] = field(default_factory=lambda: {
        "lookback": 126,
        "trend_ma": 100,
        "top_n": 20,
        "rebal_freq": "M",
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "ascending": False,
    })

    def _precompute(self, panel: PricePanel) -> None:
        lookback = int(self.params["lookback"])
        trend_ma = int(self.params["trend_ma"])
        adv_floor = float(self.params["adv_floor"])
        price_floor = float(self.params["price_floor"])

        ac = panel.adj_close
        # Score = trailing N-day return
        self._score_matrix = ac.pct_change(lookback)
        # Own-trend filter: above own MA
        ma = ac.rolling(trend_ma, min_periods=trend_ma // 2).mean()
        in_trend = ac > ma
        # Positive return filter
        positive_ret = self._score_matrix > 0
        # Activity
        active = (panel.volume_rm.fillna(0) > 0).rolling(5, min_periods=1).max() > 0
        adv20 = panel.volume_rm.rolling(20, min_periods=1).mean()

        self._elig_matrix = (
            (adv20 >= adv_floor)
            & (ac >= price_floor)
            & active
            & in_trend
            & positive_ret
            & self._score_matrix.notna()
        )
        self._cached_panel_id = id(panel)

    def _ensure_cache(self, panel: PricePanel) -> None:
        if getattr(self, "_cached_panel_id", None) != id(panel):
            self._precompute(panel)

    def eligibility(self, t: pd.Timestamp, panel: PricePanel) -> set[str]:
        self._ensure_cache(panel)
        if t not in self._elig_matrix.index:
            return set()
        row = self._elig_matrix.loc[t]
        return set(row.index[row.fillna(False)])

    def score(self, t: pd.Timestamp, panel: PricePanel, eligible: set[str]) -> pd.Series:
        self._ensure_cache(panel)
        if t not in self._score_matrix.index:
            return pd.Series(dtype=float)
        s = self._score_matrix.loc[t]
        return s[s.index.isin(eligible)].dropna()

    def rebal_dates(self, panel: PricePanel) -> list[pd.Timestamp]:
        freq = str(self.params["rebal_freq"])
        freq_map = {"W": "W-MON", "2W": "2W-MON", "M": "BMS", "Q": "BQS"}
        rule = freq_map.get(freq, "BMS")
        candidates = pd.date_range(panel.dates.min(), panel.dates.max(), freq=rule)
        all_dates_sorted = panel.dates.sort_values()
        snapped = []
        for c in candidates:
            pos = all_dates_sorted.searchsorted(c)
            if pos < len(all_dates_sorted):
                snapped.append(all_dates_sorted[pos])
        return sorted(set(snapped))
