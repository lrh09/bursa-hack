"""Short-horizon mean-reversion.

Identical eligibility / sizing to momentum. The signal is the SAME return-over-
lookback formula but `ascending=True` so the engine picks the LOSERS.

Default params reflect the literature on short-horizon reversal: 5-21 day
lookback, no skip, weekly or monthly rebalance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.momentum import Momentum


@dataclass
class Reversal(Momentum):
    """Short-horizon reversal: pick the biggest recent losers."""
    DISPLAY_NAME:  ClassVar[str] = "Short-Horizon Reversal"
    SHORT_BLURB:   ClassVar[str] = "Buy losers, sell winners over a short window (1-3 weeks)."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("rebal_freq",)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("lookback", "skip", "top_n")
    DEFINITION_MD: ClassVar[str] = """## Definition

Sort cross-section ASCENDING on `lookback`-day cumulative return — buy the
worst recent performers (mean-reversion). Skip `skip` days. Top-N
equal-weighted. The shape switch is rebal frequency (weekly vs monthly).
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "Short-term Reversals", "author": "Da, Liu, Schaumburg", "year": 2014},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/reversal.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"

    name: str = "reversal"
    params: dict[str, Any] = field(default_factory=lambda: {
        "lookback": 21,
        "skip": 0,
        "top_n": 20,
        "rebal_freq": "M",
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "ascending": True,    # pick losers
    })
