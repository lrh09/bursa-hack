"""RH's dual-slope rotation strategy (ported from the user's original Python).

Score = average of two annualised exp-regression slopes (default 30 and 60 days),
each weighted by R-squared. Picks the top N by score. Weights by inverse rolling
vol with a per-name concentration cap. Monthly rebal.

Faithful to the source script with two deliberate substitutions:
  - Fees come from the BursaHack engine's MPlus model (the broker RH actually
    uses) rather than the source's 0.08% / 0.05% / RM 8 tiered model.
  - Fills happen at T+1 OPEN per the engine's discipline (the original was
    written to fill at the rebal date's open, which lookahead-leaks the
    score-vs-fill ordering; T+1 is the bias-free version).

Source-faithful pieces:
  - Score:        100 * ((1 + slope_b)^250 - 1) * r_squared
  - Vol filter:   0.01 < period_vol_90 < max_ann_vol
  - Min score:    score > min_slope (default 20)
  - Weighting:    inverse rolling-vol parity with per-name cap (default 10%)
  - Universe:     volume > 0 today
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy
from bursahack.signals.helpers import (
    exp_regression_slope_r2,
    inv_vol_parity,
    rolling_period_vol,
)


@dataclass
class DualSlopeRotation(Strategy):
    DISPLAY_NAME:  ClassVar[str] = "Bursa Momentum Rotation (dual-slope)"
    SHORT_BLURB:   ClassVar[str] = "Composite 30+90-day annualised log-slope, top-N inverse-vol weighted with per-name cap."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("rebal_freq",)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("slope_lookback_short", "slope_lookback_long", "vol_period", "min_slope", "top_n", "weight_cap")
    DEFINITION_MD: ClassVar[str] = """## Definition

RH's original dual-slope rotation. Composite score = avg of two annualised
exp-regression slopes (default 30d + 90d), each weighted by R^2. Picks
top-N by score. Weights by inverse rolling vol with a per-name
concentration cap (default 10%). Rebal frequency is the shape switch.
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "RH original Python (port preserved bug-for-bug)", "year": 2024},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/rotation.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"

    name: str = "rotation"
    params: dict[str, Any] = field(default_factory=lambda: {
        "slope_lookback_short": 30,
        "slope_lookback_long": 60,
        "vol_period": 90,
        "min_period_vol": 0.01,
        "max_period_vol": 0.40,
        "min_slope": 20.0,            # score threshold, post 100x scaling
        "top_n": 30,
        "weight_cap": 0.10,
        "rebal_freq": "M",
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "ascending": False,
    })

    def _precompute(self, panel: PricePanel) -> None:
        p = self.params
        ac = panel.adj_close

        # Two slopes (annualised compound style to match the source script)
        ann_s_short, r2_s = exp_regression_slope_r2(
            ac, lookback=int(p["slope_lookback_short"]),
            trading_days_per_year=250, style="compound",
        )
        ann_s_long, r2_l = exp_regression_slope_r2(
            ac, lookback=int(p["slope_lookback_long"]),
            trading_days_per_year=250, style="compound",
        )
        score_short = 100.0 * ann_s_short * r2_s
        score_long = 100.0 * ann_s_long * r2_l
        self._score_matrix = 0.5 * (score_short + score_long)

        # Period-vol used for both filtering and weighting
        self._vol = rolling_period_vol(ac, period=int(p["vol_period"]))

        # Eligibility mask
        adv20 = panel.volume_rm.rolling(20, min_periods=1).mean()
        active = (panel.volume_rm.fillna(0) > 0).rolling(5, min_periods=1).max() > 0
        elig = (
            (adv20 >= float(p["adv_floor"]))
            & (ac >= float(p["price_floor"]))
            & active
            & (self._vol > float(p["min_period_vol"]))
            & (self._vol < float(p["max_period_vol"]))
            & self._score_matrix.notna()
            & self._vol.notna()
        )
        self._elig_matrix = elig
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
        s = s[s.index.isin(eligible)]
        # min_slope gate (source: `eligible[eligible['avg_slope'] > min_slope]`)
        min_slope = float(self.params["min_slope"])
        s = s[s > min_slope]
        return s.dropna()

    def weights(self, t: pd.Timestamp, panel: PricePanel) -> pd.Series:
        """Override base to apply inverse-vol parity with concentration cap."""
        eligible = self.eligibility(t, panel)
        if not eligible:
            return pd.Series(dtype=float)
        scores = self.score(t, panel, eligible)
        if scores.empty:
            return pd.Series(dtype=float)

        top_n = int(self.params["top_n"])
        ascending = bool(self.params["ascending"])
        picked = scores.sort_values(ascending=ascending).head(top_n).index

        vol_t = self._vol.loc[t, picked] if t in self._vol.index else pd.Series(np.nan, index=picked)
        vol_t = vol_t.fillna(vol_t.median())
        weight_cap = float(self.params["weight_cap"])
        return inv_vol_parity(vol_t, weight_cap=weight_cap)

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
