"""Donchian / 52-week-high breakout (classic Turtle-style).

For each stock, compute the trailing `lookback`-day high. A stock is in
"breakout" state if its current close is at or near that high.

Score = ratio of current close to lookback high. Names above the breakout
threshold get high scores; otherwise excluded.

Combined with a trend filter (above own MA) and the usual liquidity gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy


@dataclass
class DonchianBreakout(Strategy):
    DISPLAY_NAME:  ClassVar[str] = "Donchian Breakout"
    SHORT_BLURB:   ClassVar[str] = "Score by close/lookback-high; include if within breakout_thresh of the high and above trend MA."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("rebal_freq",)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("lookback", "breakout_thresh", "trend_ma", "top_n")
    DEFINITION_MD: ClassVar[str] = """## Definition

For each stock, compute the trailing `lookback`-day high. Score =
`close / lookback_high`. A name is eligible if score >=
`breakout_thresh` (default 0.95, i.e. within 5% of the high) AND price
is above its own `trend_ma`-day moving average. Top-N by score, equal
weighted. Channel breakout in the Donchian / turtle tradition.
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "Way of the Turtle", "author": "Curtis Faith", "year": 2007},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/breakout.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"

    name: str = "breakout"
    params: dict[str, Any] = field(default_factory=lambda: {
        "lookback": 252,           # 52-week high default
        "breakout_thresh": 0.95,   # within 5% of high counts as a breakout
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
        threshold = float(self.params["breakout_thresh"])
        adv_floor = float(self.params["adv_floor"])
        price_floor = float(self.params["price_floor"])

        ac = panel.adj_close
        rolling_max = ac.rolling(lookback, min_periods=lookback // 2).max()
        # Score = close / lookback_high  (1.0 = at the high, lower = further from)
        self._score_matrix = ac / rolling_max
        is_breakout = self._score_matrix >= threshold

        ma = ac.rolling(trend_ma, min_periods=trend_ma // 2).mean()
        in_trend = ac > ma
        active = (panel.volume_rm.fillna(0) > 0).rolling(5, min_periods=1).max() > 0
        adv20 = panel.volume_rm.rolling(20, min_periods=1).mean()

        self._elig_matrix = (
            (adv20 >= adv_floor)
            & (ac >= price_floor)
            & active
            & in_trend
            & is_breakout
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
