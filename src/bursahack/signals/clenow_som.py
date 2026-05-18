"""Andreas Clenow — "Stocks on the Move" (adapted to Bursa Malaysia).

Faithful to the book's recipe with one substitution: no explicit FBM KLCI
series in our data, so the index regime filter uses an internally-constructed
equal-weight basket of the top-30 most-liquid securities as a market proxy.

Recipe (parameters tunable):
  1. Regime filter (gate, optional via `use_regime`):
       Trade only when market_proxy > its `regime_ma`-day moving average.
       When off, hold cash.
  2. Stock universe (per-date):
       - vanilla equity (handled at data-loader / engine level)
       - price >= price_floor, 20d ADV >= adv_floor
       - Above its `trend_ma`-day moving average
       - No single-day |return| > `max_gap` in last `lookback` days
  3. Score (rank by, descending):
       annualised_exp_regression_slope * r_squared
       over the last `lookback` trading days.
  4. Pick top `top_n` by score.
  5. Position sizing (inverse-ATR, normalised):
       raw_size_i = risk_per_position / ATR_i
       weight_i   = raw_size_i / sum(raw_size)
     This is Clenow's "risk parity by ATR", scaled so that gross = 100%.
  6. Rebalance every `rebal_freq` (W / 2W / M / Q).

The book's exact rule does intra-rebal trim-down for positions that breach
the trend/gap filters between rebal dates. We approximate by force-exiting
those names at the next rebal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy
from bursahack.signals.helpers import (
    atr,
    exp_regression_slope_r2,
    gap_filter,
    market_proxy,
    regime_filter,
    trend_filter,
)


@dataclass
class ClenowSOM(Strategy):
    DISPLAY_NAME:  ClassVar[str] = "Clenow Stocks on the Move"
    SHORT_BLURB:   ClassVar[str] = "Exp-regression slope x R^2, ATR-sized, top-N monthly. Trend-following with a market-regime filter."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("use_regime", "rebal_freq")
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("lookback", "trend_ma", "regime_ma", "top_n", "atr_window", "max_gap")
    DEFINITION_MD: ClassVar[str] = """## Definition

Score = annualised exp-regression slope x R^2 over `lookback` days.
Position sized inverse-ATR(`atr_window`). Eligibility gate: price above
`trend_ma` and not above-`max_gap` from prior close. Regime filter: when
`use_regime` is true, equity market proxy must be above `regime_ma`
otherwise the strategy goes to cash.
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "Stocks on the Move", "author": "Andreas Clenow", "year": 2015},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/clenow_som.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"

    name: str = "clenow_som"
    params: dict[str, Any] = field(default_factory=lambda: {
        "lookback": 90,                # exp-regression window
        "trend_ma": 100,               # per-stock trend filter
        "regime_ma": 200,              # market regime filter
        "atr_window": 20,
        "max_gap": 0.15,
        "top_n": 30,
        "rebal_freq": "M",             # tunable
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "use_regime": True,
        "ascending": False,
    })

    def _precompute(self, panel: PricePanel) -> None:
        lookback = int(self.params["lookback"])
        trend_ma = int(self.params["trend_ma"])
        regime_ma = int(self.params["regime_ma"])
        atr_window = int(self.params["atr_window"])
        max_gap = float(self.params["max_gap"])
        adv_floor = float(self.params["adv_floor"])
        price_floor = float(self.params["price_floor"])

        ac = panel.adj_close

        # Score
        ann_slope, r2 = exp_regression_slope_r2(ac, lookback=lookback)
        self._score_matrix = (ann_slope * r2).replace([np.inf, -np.inf], np.nan)

        # ATR for position sizing
        self._atr = atr(panel, window=atr_window)

        # Per-stock filters
        adv20 = panel.volume_rm.rolling(20, min_periods=1).mean()
        active = (panel.volume_rm.fillna(0) > 0).rolling(5, min_periods=1).max() > 0
        in_trend = trend_filter(ac, ma_window=trend_ma)
        no_gap = gap_filter(ac, lookback=lookback, max_gap=max_gap)

        elig = (
            (adv20 >= adv_floor)
            & (ac >= price_floor)
            & active
            & in_trend
            & no_gap
            & self._score_matrix.notna()
        )
        self._elig_matrix = elig

        # Market regime
        if self.params["use_regime"]:
            mkt = market_proxy(panel, top_n_by_adv=30, window=regime_ma)
            self._regime_on = regime_filter(mkt, ma_window=regime_ma)
        else:
            self._regime_on = pd.Series(True, index=ac.index)

        self._cached_panel_id = id(panel)

    def _ensure_cache(self, panel: PricePanel) -> None:
        if getattr(self, "_cached_panel_id", None) != id(panel):
            self._precompute(panel)

    # ---- Strategy API ----------------------------------------------------
    def eligibility(self, t: pd.Timestamp, panel: PricePanel) -> set[str]:
        self._ensure_cache(panel)
        if t not in self._elig_matrix.index:
            return set()
        # Regime filter: if market is off, no positions
        if self.params["use_regime"]:
            on = bool(self._regime_on.get(t, False))
            if not on:
                return set()
        row = self._elig_matrix.loc[t]
        return set(row.index[row.fillna(False)])

    def score(self, t: pd.Timestamp, panel: PricePanel, eligible: set[str]) -> pd.Series:
        self._ensure_cache(panel)
        if t not in self._score_matrix.index:
            return pd.Series(dtype=float)
        s = self._score_matrix.loc[t]
        s = s[s.index.isin(eligible)]
        return s.dropna()

    def weights(self, t: pd.Timestamp, panel: PricePanel) -> pd.Series:
        """Override: ATR-based inverse-vol weighting normalised to 100% gross."""
        eligible = self.eligibility(t, panel)
        if not eligible:
            return pd.Series(dtype=float)
        scores = self.score(t, panel, eligible)
        if scores.empty:
            return pd.Series(dtype=float)
        top_n = int(self.params["top_n"])
        ascending = bool(self.params["ascending"])
        picked = scores.sort_values(ascending=ascending).head(top_n).index

        # Inverse-percentage-vol weighting (Clenow's risk parity by ATR).
        # Per the book: position_notional ~ account * risk / (ATR / price)
        # so weight is proportional to price / ATR == 1 / percent_vol.
        atr_t = self._atr.loc[t, picked] if t in self._atr.index else pd.Series(np.nan, index=picked)
        px_t = panel.adj_close.loc[t, picked]
        pct_vol = (atr_t / px_t).replace([np.inf, -np.inf], np.nan)
        pct_vol = pct_vol.fillna(pct_vol.median()).clip(lower=1e-4)
        raw_size = 1.0 / pct_vol
        w = raw_size / raw_size.sum()
        return w

    def rebal_dates(self, panel: PricePanel) -> list[pd.Timestamp]:
        freq = str(self.params["rebal_freq"])
        freq_map = {
            "W": "W-MON",       # weekly
            "2W": "2W-MON",     # bi-weekly (the book's original)
            "M": "BMS",
            "Q": "BQS",
        }
        rule = freq_map.get(freq, "BMS")
        candidates = pd.date_range(panel.dates.min(), panel.dates.max(), freq=rule)
        all_dates_sorted = panel.dates.sort_values()
        snapped = []
        for c in candidates:
            pos = all_dates_sorted.searchsorted(c)
            if pos < len(all_dates_sorted):
                snapped.append(all_dates_sorted[pos])
        return sorted(set(snapped))
