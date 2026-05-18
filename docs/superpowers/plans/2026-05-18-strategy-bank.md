# Strategy Bank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse 186 parameter variants into ~12-18 auto-derived "strategy bank" entries, surface them via a new `/strategies` index + refactored detail pages with a variant explorer, while preserving existing URLs via redirects.

**Architecture:** Python registry declared as `ClassVar` attributes on each `Strategy` subclass → `signals/registry.py` groups search-log rows by `(family, shape_tuple)` → `scripts/build_data.py` emits one bundle per strategy + alias table + hash-to-id mapping. FE consumes the new bundle shape, adds bank index route, slide-over for variant detail, demotes `/search`, widens `/compare`.

**Tech Stack:** Python 3.11 + pandas + pytest (data pipeline) · Next.js 15 + Tailwind v4 + shadcn/ui + Plotly basic + Recharts (FE) · Playwright (smoke).

**Spec:** `docs/superpowers/specs/2026-05-18-strategy-bank-design.md` (read this first if confused about any task).

**Iron rules** (carried through every task):
- Do NOT re-touch the 2020-2022 holdout. `build_data.py` is read-only over `results/`.
- Never write `RAILWAY_TOKEN` or anything from `BursaHack/.env` into a committed file. `.env` is already gitignored.
- No `Co-Authored-By: Claude` trailer on any commit.
- Personal repo: commits land on `master`, push directly.

---

## Phase 1 — Python registry foundation (Tasks 1-4)

### Task 1: Add bank-metadata ClassVars + defaults to base `Strategy`

**Files:**
- Modify: `src/bursahack/signals/base.py`
- Test: `tests/test_strategy_base_metadata.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_base_metadata.py`:

```python
"""Tests for Strategy base-class bank-metadata ClassVars."""
from __future__ import annotations

import pandas as pd
import pytest

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy


class _Dummy(Strategy):
    """Minimal subclass for testing defaults."""
    DISPLAY_NAME = "Dummy"
    SHAPE_KEYS = ()
    SHORT_BLURB = "test"
    DEFINITION_MD = "## Dummy\nfor tests"
    SOURCE_FILE = "tests/test_strategy_base_metadata.py"
    ADDED = "2026-05-18"

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


def test_holdout_locked_default_true():
    assert Strategy.HOLDOUT_LOCKED is True


def test_shape_keys_default_empty_tuple():
    assert Strategy.SHAPE_KEYS == ()


def test_cont_keys_default_empty_tuple():
    assert Strategy.CONT_KEYS == ()


def test_references_default_empty_tuple():
    assert Strategy.REFERENCES == ()


def test_headline_rule_default_max_wf_sharpe():
    assert Strategy.HEADLINE_RULE == "max wf_sharpe"


def test_dummy_inherits_defaults():
    assert _Dummy.HOLDOUT_LOCKED is True
    assert _Dummy.HEADLINE_RULE == "max wf_sharpe"
```

- [ ] **Step 2: Run tests, verify failure**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_strategy_base_metadata.py -v`
Expected: `AttributeError: type object 'Strategy' has no attribute 'HOLDOUT_LOCKED'` (or similar).

- [ ] **Step 3: Add the ClassVars to `Strategy`**

Edit `src/bursahack/signals/base.py`. Replace the `import` block and the `class Strategy` opening with:

```python
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
```

Leave the abstract methods and `weights`/`signal_fn` methods exactly as they are below (no other changes to this file).

- [ ] **Step 4: Run tests, verify pass**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_strategy_base_metadata.py -v`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add src/bursahack/signals/base.py tests/test_strategy_base_metadata.py
git commit -m "feat(signals): bank-metadata ClassVars on Strategy base

Adds DISPLAY_NAME / SHORT_BLURB / SHAPE_KEYS / CONT_KEYS / DEFINITION_MD /
REFERENCES / SOURCE_FILE / ADDED / HEADLINE_RULE / HOLDOUT_LOCKED ClassVars
with safe defaults. Drives the strategy bank (spec §3.1).

Subsequent tasks fill them in per signal subclass and wire the registry."
```

---

### Task 2: Create `signals/registry.py` with grouping + id generation

**Files:**
- Create: `src/bursahack/signals/registry.py`
- Test: `tests/test_strategy_registry.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_registry.py`:

```python
"""Tests for the strategy registry: id generation + variant grouping."""
from __future__ import annotations

import pandas as pd
import pytest

from bursahack.signals.base import Strategy
from bursahack.signals.registry import (
    all_strategies,
    group_variants_by_strategy,
    strategy_id_for,
)


class _RegimeStrat(Strategy):
    DISPLAY_NAME = "Regime test"
    SHAPE_KEYS = ("use_regime", "rebal_freq")
    CONT_KEYS = ("lookback",)
    SHORT_BLURB = "x"
    DEFINITION_MD = "x"
    SOURCE_FILE = "x"
    ADDED = "2026-05-18"

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


class _NoShape(Strategy):
    DISPLAY_NAME = "No shape"
    SHAPE_KEYS = ()
    SHORT_BLURB = "x"
    DEFINITION_MD = "x"
    SOURCE_FILE = "x"
    ADDED = "2026-05-18"

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


def test_strategy_id_no_shape_keys_returns_family_only():
    # Strategy with no SHAPE_KEYS -> bare family name
    sid = strategy_id_for("donchian_breakout", _NoShape.SHAPE_KEYS, {"lookback": 60})
    assert sid == "donchian_breakout"


def test_strategy_id_bool_normalised():
    sid = strategy_id_for(
        "clenow_som",
        ("use_regime", "rebal_freq"),
        {"use_regime": True, "rebal_freq": "M", "lookback": 60},
    )
    assert sid == "clenow_som__regime-on__rebal-M"


def test_strategy_id_bool_false_normalised():
    sid = strategy_id_for(
        "clenow_som",
        ("use_regime", "rebal_freq"),
        {"use_regime": False, "rebal_freq": "W"},
    )
    assert sid == "clenow_som__regime-off__rebal-W"


def test_strategy_id_declaration_order_preserved():
    # Swap the value-dict order; output must reflect SHAPE_KEYS order.
    sid_a = strategy_id_for(
        "x", ("a", "b"), {"b": "B", "a": "A"},
    )
    sid_b = strategy_id_for(
        "x", ("a", "b"), {"a": "A", "b": "B"},
    )
    assert sid_a == sid_b == "x__a-A__b-B"


def test_strategy_id_string_freq_lowercased_with_prefix():
    sid = strategy_id_for("rotation", ("rebal_freq",), {"rebal_freq": "Q"})
    assert sid == "rotation__rebal-Q"


def test_group_variants_by_strategy_collapses_continuous_only_variants():
    # 3 variants, all same shape (regime-on, M), differ only on continuous
    # lookback -> all roll up into ONE strategy.
    rows = [
        {"strategy": "clenow_som", "params_hash": "h1",
         "params": {"use_regime": True, "rebal_freq": "M", "lookback": 60}},
        {"strategy": "clenow_som", "params_hash": "h2",
         "params": {"use_regime": True, "rebal_freq": "M", "lookback": 90}},
        {"strategy": "clenow_som", "params_hash": "h3",
         "params": {"use_regime": True, "rebal_freq": "M", "lookback": 120}},
    ]
    shape_keys_for = {"clenow_som": ("use_regime", "rebal_freq")}
    grouped = group_variants_by_strategy(rows, shape_keys_for)
    assert len(grouped) == 1
    sid, variants = next(iter(grouped.items()))
    assert sid == "clenow_som__regime-on__rebal-M"
    assert len(variants) == 3


def test_group_variants_by_strategy_splits_on_shape_difference():
    rows = [
        {"strategy": "clenow_som", "params_hash": "h1",
         "params": {"use_regime": True,  "rebal_freq": "M", "lookback": 60}},
        {"strategy": "clenow_som", "params_hash": "h2",
         "params": {"use_regime": False, "rebal_freq": "M", "lookback": 60}},
        {"strategy": "clenow_som", "params_hash": "h3",
         "params": {"use_regime": True,  "rebal_freq": "W", "lookback": 60}},
    ]
    shape_keys_for = {"clenow_som": ("use_regime", "rebal_freq")}
    grouped = group_variants_by_strategy(rows, shape_keys_for)
    assert set(grouped.keys()) == {
        "clenow_som__regime-on__rebal-M",
        "clenow_som__regime-off__rebal-M",
        "clenow_som__regime-on__rebal-W",
    }
    for variants in grouped.values():
        assert len(variants) == 1
```

- [ ] **Step 2: Run tests, verify failure**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_strategy_registry.py -v`
Expected: `ModuleNotFoundError: No module named 'bursahack.signals.registry'`.

- [ ] **Step 3: Implement the registry**

Create `src/bursahack/signals/registry.py`:

```python
"""Strategy bank registry.

Reads the `Strategy.__subclasses__()` tree to discover declared strategies,
generates stable `strategy_id` slugs, and groups raw search-log variant rows
by `(family, shape_tuple)`.

The strategy_id format encodes shape-key values in *declaration order* (the
order they appear in `Strategy.SHAPE_KEYS`), so re-ordering a dict elsewhere
never changes a URL.

See: docs/superpowers/specs/2026-05-18-strategy-bank-design.md §3.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from bursahack.signals.base import Strategy


def all_strategies() -> list[type[Strategy]]:
    """Every concrete Strategy subclass currently imported.

    Note: only subclasses that have been *imported* show up. The single
    import-everything point is `bursahack.signals.__init__`. Callers should
    `import bursahack.signals` once before calling this.
    """
    seen: set[type[Strategy]] = set()
    out: list[type[Strategy]] = []
    stack: list[type[Strategy]] = list(Strategy.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        # Skip ABCs / test stubs that didn't declare DISPLAY_NAME
        if cls.DISPLAY_NAME:
            out.append(cls)
        stack.extend(cls.__subclasses__())
    return out


def _slug_for_value(key: str, value: Any) -> str:
    """Normalise a shape-key value into the URL token.

    - booleans:  use_regime=True  -> "regime-on"
                 use_regime=False -> "regime-off"
                 generic bool key:  "{key}-on" / "{key}-off"
    - strings (freq codes):       "rebal-{value}"   (value kept as-is, no case fold)
    - everything else:            "{key}-{value}"
    """
    if isinstance(value, bool):
        if key == "use_regime":
            return "regime-on" if value else "regime-off"
        return f"{key}-{'on' if value else 'off'}"
    if key == "rebal_freq":
        return f"rebal-{value}"
    return f"{key}-{value}"


def strategy_id_for(
    family: str,
    shape_keys: tuple[str, ...],
    params: dict[str, Any],
) -> str:
    """Generate the strategy_id slug for a (family, shape_tuple).

    No shape keys -> bare family. Otherwise:
        family__token1__token2... in SHAPE_KEYS declaration order.
    """
    if not shape_keys:
        return family
    parts = [family]
    for k in shape_keys:
        if k not in params:
            raise KeyError(f"shape key {k!r} missing from params for family {family!r}")
        parts.append(_slug_for_value(k, params[k]))
    return "__".join(parts)


def group_variants_by_strategy(
    rows: Iterable[dict],
    shape_keys_for: dict[str, tuple[str, ...]],
) -> dict[str, list[dict]]:
    """Bucket variant rows by (family, shape_tuple) into strategy_ids.

    `rows` is any iterable of dicts with keys at least `strategy` (family
    name) and `params` (the full param dict). `shape_keys_for` maps a
    family to its declared SHAPE_KEYS.

    Returns dict[strategy_id, list[row]]; order is insertion order
    (deterministic given deterministic row order).
    """
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        family = row["strategy"]
        shape_keys = shape_keys_for.get(family, ())
        sid = strategy_id_for(family, shape_keys, row["params"])
        out[sid].append(row)
    return dict(out)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_strategy_registry.py -v`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add src/bursahack/signals/registry.py tests/test_strategy_registry.py
git commit -m "feat(signals): registry with strategy_id generation + grouping

Implements signals/registry.py:
- all_strategies() walks Strategy.__subclasses__() (requires
  bursahack.signals to have been imported)
- strategy_id_for(family, shape_keys, params) emits stable slugs:
  family__token1__token2 in SHAPE_KEYS declaration order
- _slug_for_value normalises bools (use_regime->regime-on/off) and
  rebal_freq->rebal-X
- group_variants_by_strategy(rows, shape_keys_for) buckets search-log
  rows by (family, shape_tuple)

7/7 tests pass. Spec §3.2 + §3.3."
```

---

### Task 3: Populate bank metadata on all 6 signal subclasses

**Files:**
- Modify: `src/bursahack/signals/clenow_som.py`
- Modify: `src/bursahack/signals/rotation.py`
- Modify: `src/bursahack/signals/momentum.py`
- Modify: `src/bursahack/signals/reversal.py`
- Modify: `src/bursahack/signals/timeseries_momentum.py`
- Modify: `src/bursahack/signals/breakout.py`
- Modify: `src/bursahack/signals/__init__.py`
- Test: `tests/test_signal_metadata.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_signal_metadata.py`:

```python
"""All Strategy subclasses must declare the mandatory bank-metadata fields."""
from __future__ import annotations

import pytest

# Import side-effect: registers every Strategy subclass.
import bursahack.signals  # noqa: F401
from bursahack.signals.registry import all_strategies


MANDATORY = ("DISPLAY_NAME", "SHAPE_KEYS", "SHORT_BLURB",
             "DEFINITION_MD", "SOURCE_FILE", "ADDED")


@pytest.mark.parametrize("cls", all_strategies(), ids=lambda c: c.__name__)
def test_subclass_has_mandatory_metadata(cls):
    missing = []
    for f in MANDATORY:
        value = getattr(cls, f, "")
        if value == "" or value is None:
            missing.append(f)
    assert not missing, f"{cls.__name__} missing mandatory metadata: {missing}"


def test_at_least_six_strategies_registered():
    families = {c().name if hasattr(c, "__init__") else c.__name__ for c in all_strategies()}
    # 6 expected families today: rotation, clenow_som, momentum, reversal,
    # tsmom (Timeseries Momentum), donchian_breakout
    assert len(all_strategies()) >= 6
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_signal_metadata.py -v`
Expected: parametrised failures listing missing metadata per class.

- [ ] **Step 3: Add metadata to all 6 signal subclasses**

For each file, find the class declaration (e.g. `@dataclass\nclass ClenowSOM(Strategy):`) and insert the ClassVar block immediately after the class line (above the existing `name: str = ...` line). Pattern:

```python
    DISPLAY_NAME:  ClassVar[str] = "<human label>"
    SHORT_BLURB:   ClassVar[str] = "<one-sentence>"
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = (...)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = (...)
    DEFINITION_MD: ClassVar[str] = """## <Title>\n..."""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (...)
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/<file>.py"
    ADDED:         ClassVar[str] = "<YYYY-MM-DD>"
    HEADLINE_RULE: ClassVar[str] = "<rule>"
```

Add `from typing import ClassVar` to the imports if not present.

**Per-file values:**

`clenow_som.py` → `class ClenowSOM`:
```python
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
```

`rotation.py` → `class DualSlopeRotation`:
```python
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
```

`momentum.py` → `class Momentum`:
```python
    DISPLAY_NAME:  ClassVar[str] = "Cross-Sectional Momentum"
    SHORT_BLURB:   ClassVar[str] = "Classic 12-1 (or shorter) cumulative-return ranking, top-N equal weight."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("rebal_freq",)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("lookback", "skip", "top_n")
    DEFINITION_MD: ClassVar[str] = """## Definition

Cross-sectional momentum on cumulative returns over `lookback` days,
skipping the most recent `skip` days (the classic 12-1 form for monthly
rebal). Top-N by score, equal weighted.
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "Returns to Buying Winners and Selling Losers", "author": "Jegadeesh & Titman", "year": 1993},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/momentum.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"
```

`reversal.py` → `class Reversal`:
```python
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
```

`timeseries_momentum.py` → `class TimeseriesMomentum` (use the actual class name in the file; if different, adapt):
```python
    DISPLAY_NAME:  ClassVar[str] = "Time-Series Momentum (TSMOM)"
    SHORT_BLURB:   ClassVar[str] = "Per-name trend filter: hold if N-day return is positive, else flat."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("rebal_freq",)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("lookback", "top_n")
    DEFINITION_MD: ClassVar[str] = """## Definition

Per-security trend filter: long only if the trailing `lookback`-day
return is positive, otherwise flat. Top-N by absolute trend strength.
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "Time Series Momentum", "author": "Moskowitz, Ooi, Pedersen", "year": 2012},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/timeseries_momentum.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"
```

`breakout.py` → `class DonchianBreakout` (use actual class name):
```python
    DISPLAY_NAME:  ClassVar[str] = "Donchian Breakout"
    SHORT_BLURB:   ClassVar[str] = "Buy on N-day high break, exit on N-day low. Classic turtle-style channel breakout."
    SHAPE_KEYS:    ClassVar[tuple[str, ...]] = ("rebal_freq",)
    CONT_KEYS:     ClassVar[tuple[str, ...]] = ("lookback_high", "lookback_low", "top_n")
    DEFINITION_MD: ClassVar[str] = """## Definition

Long on close above the `lookback_high`-day high; exit on close below
the `lookback_low`-day low. Channel breakout in the Donchian / turtle
tradition.
"""
    REFERENCES:    ClassVar[tuple[dict, ...]] = (
        {"title": "Way of the Turtle", "author": "Curtis Faith", "year": 2007},
    )
    SOURCE_FILE:   ClassVar[str] = "src/bursahack/signals/breakout.py"
    ADDED:         ClassVar[str] = "2026-04-12"
    HEADLINE_RULE: ClassVar[str] = "max wf_sharpe"
```

If any actual class name differs from these (e.g. `TSMOM` instead of `TimeseriesMomentum`), use the actual class name from the file — don't rename.

- [ ] **Step 2b: Ensure all signal classes are imported via `signals/__init__.py`**

Edit `src/bursahack/signals/__init__.py` (currently empty) to:

```python
"""Re-export every Strategy subclass so `Strategy.__subclasses__()` sees them.

The registry walks the subclass tree; classes only appear there if their
defining module has been imported. This file is the single import point.
"""
from bursahack.signals.breakout import *  # noqa: F401, F403
from bursahack.signals.clenow_som import *  # noqa: F401, F403
from bursahack.signals.momentum import *  # noqa: F401, F403
from bursahack.signals.reversal import *  # noqa: F401, F403
from bursahack.signals.rotation import *  # noqa: F401, F403
from bursahack.signals.timeseries_momentum import *  # noqa: F401, F403
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_signal_metadata.py -v`
Expected: 6+ parametrised tests pass, `test_at_least_six_strategies_registered` passes.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add src/bursahack/signals/ tests/test_signal_metadata.py
git commit -m "feat(signals): bank metadata on all 6 strategy families

Adds DISPLAY_NAME / SHAPE_KEYS / CONT_KEYS / DEFINITION_MD / etc. to
ClenowSOM, DualSlopeRotation, Momentum, Reversal, TSMOM, DonchianBreakout.

SHAPE_KEYS assignment per family:
- clenow_som: (use_regime, rebal_freq)  -- 2 shape switches
- rotation:   (rebal_freq,)             -- 1
- momentum:   (rebal_freq,)             -- 1
- reversal:   (rebal_freq,)             -- 1
- tsmom:      (rebal_freq,)             -- 1
- breakout:   (rebal_freq,)             -- 1

signals/__init__.py now imports every submodule so all subclasses are
discoverable via Strategy.__subclasses__(). Spec §3.1 mandatory floor met."
```

---

### Task 4: Add `HOLDOUT_LOCKED` guard to `run_search.py`

**Files:**
- Modify: `src/bursahack/run_search.py`
- Test: `tests/test_run_search_guard.py` (new)

- [ ] **Step 1: Read the relevant section to find the right spot**

Read `src/bursahack/run_search.py` and identify the function that creates and runs folds. We want the guard right before backtests dispatch.

- [ ] **Step 2: Write the failing test**

Create `tests/test_run_search_guard.py`:

```python
"""HOLDOUT_LOCKED guard: refuse to run if any fold validate_end > 2019-12-31
unless the strategy class declares HOLDOUT_LOCKED=False AND --touch-holdout
is passed."""
from __future__ import annotations

import pandas as pd
import pytest

from bursahack.signals.base import Strategy
from bursahack.run_search import assert_holdout_safe


class _Locked(Strategy):
    DISPLAY_NAME = "L"; SHAPE_KEYS = (); SHORT_BLURB = "x"; DEFINITION_MD = "x"
    SOURCE_FILE = "x"; ADDED = "2026-05-18"
    # HOLDOUT_LOCKED defaults True

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


class _Unlocked(Strategy):
    DISPLAY_NAME = "U"; SHAPE_KEYS = (); SHORT_BLURB = "x"; DEFINITION_MD = "x"
    SOURCE_FILE = "x"; ADDED = "2026-05-18"
    HOLDOUT_LOCKED = False

    def eligibility(self, t, panel): return set()
    def score(self, t, panel, eligible): return pd.Series(dtype=float)
    def rebal_dates(self, panel): return []


def _fold(validate_end):
    return {"validate_end": pd.Timestamp(validate_end)}


def test_in_sample_folds_always_pass():
    folds = [_fold("2018-12-31"), _fold("2019-12-31")]
    assert_holdout_safe(_Locked, folds, touch_holdout=False)  # no raise


def test_oos_fold_with_locked_class_raises():
    folds = [_fold("2018-12-31"), _fold("2020-06-30")]
    with pytest.raises(RuntimeError, match=r"holdout"):
        assert_holdout_safe(_Locked, folds, touch_holdout=False)


def test_oos_fold_with_locked_class_and_cli_flag_still_raises():
    folds = [_fold("2020-06-30")]
    with pytest.raises(RuntimeError, match=r"HOLDOUT_LOCKED"):
        assert_holdout_safe(_Locked, folds, touch_holdout=True)


def test_oos_fold_with_unlocked_class_and_no_cli_flag_raises():
    folds = [_fold("2020-06-30")]
    with pytest.raises(RuntimeError, match=r"--touch-holdout"):
        assert_holdout_safe(_Unlocked, folds, touch_holdout=False)


def test_oos_fold_with_unlocked_class_and_cli_flag_passes():
    folds = [_fold("2020-06-30")]
    assert_holdout_safe(_Unlocked, folds, touch_holdout=True)  # no raise
```

- [ ] **Step 3: Run test, verify failure**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_run_search_guard.py -v`
Expected: `ImportError: cannot import name 'assert_holdout_safe'`.

- [ ] **Step 4: Implement the guard**

Add to `src/bursahack/run_search.py` near the top (after existing imports):

```python
import pandas as pd

from bursahack.signals.base import Strategy

HOLDOUT_START = pd.Timestamp("2020-01-01")


def assert_holdout_safe(
    strategy_cls: type[Strategy],
    folds: list[dict],
    *,
    touch_holdout: bool,
) -> None:
    """Block fold execution that would touch 2020-2022 holdout data.

    Raises RuntimeError unless ALL of: strategy_cls.HOLDOUT_LOCKED is False AND
    touch_holdout is True. In-sample-only fold sets always pass.
    """
    any_oos = any(pd.Timestamp(f["validate_end"]) >= HOLDOUT_START for f in folds)
    if not any_oos:
        return
    if strategy_cls.HOLDOUT_LOCKED:
        raise RuntimeError(
            f"holdout safety: {strategy_cls.__name__} has HOLDOUT_LOCKED=True "
            f"but fold set includes validate_end >= {HOLDOUT_START.date()}. "
            f"Set HOLDOUT_LOCKED=False on the class AND pass --touch-holdout."
        )
    if not touch_holdout:
        raise RuntimeError(
            f"holdout safety: {strategy_cls.__name__} has HOLDOUT_LOCKED=False but "
            f"--touch-holdout was NOT passed; fold set includes validate_end "
            f">= {HOLDOUT_START.date()}. Refusing to run."
        )
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_run_search_guard.py -v`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add src/bursahack/run_search.py tests/test_run_search_guard.py
git commit -m "feat(run_search): HOLDOUT_LOCKED guard

assert_holdout_safe(strategy_cls, folds, touch_holdout) raises unless the
class has HOLDOUT_LOCKED=False AND --touch-holdout is passed. Spec §6.3.

Wiring into the actual run_search CLI call sites is intentionally deferred
to a follow-up; existing in-sample runs are unaffected because the guard
is opt-in until callers adopt it."
```

---

## Phase 2 — `build_data.py` rewrite (Tasks 5-6)

### Task 5: Refactor `build_data.py` to emit per-strategy bundles + alias table

**Files:**
- Modify: `scripts/build_data.py`
- Test: `tests/test_build_data_strategy_bundles.py` (new)

- [ ] **Step 1: Write the failing test (integration-style — calls main with the real data)**

Create `tests/test_build_data_strategy_bundles.py`:

```python
"""build_data.py emits one bundle per strategy_id with the right shape."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def _run_build_data():
    """Run the pipeline once for the module; tests inspect the output."""
    res = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_data.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.skip(f"build_data.py failed: {res.stderr or res.stdout}")


def _load(path: str) -> dict:
    return json.loads((REPO / "web" / "data" / path).read_text(encoding="utf-8"))


def test_manifest_has_hash_to_strategy_id_lookup():
    m = _load("manifest.json")
    assert "hash_to_strategy_id" in m
    assert isinstance(m["hash_to_strategy_id"], dict)
    assert len(m["hash_to_strategy_id"]) >= 50


def test_manifest_strategies_count_in_expected_range():
    m = _load("manifest.json")
    sids = [s["strategy_id"] for s in m["strategies"]]
    assert 6 <= len(sids) <= 30, f"expected 6-30 strategies, got {len(sids)}: {sids}"


def test_strategy_aliases_file_exists_and_has_legacy_redirects():
    aliases = _load("strategy_aliases.json")
    assert aliases.get("rotation_rank_1") == "rotation__rebal-M"
    assert aliases.get("clenow_som_rank_9") == "clenow_som__regime-on__rebal-M"


def test_each_strategy_bundle_has_required_fields():
    m = _load("manifest.json")
    required = {
        "strategy_id", "family", "display_name", "shape", "short_blurb",
        "definition_md", "references", "source_file", "added",
        "variant_count", "headline_variant_hash", "headline_reason",
        "aggregate_metrics", "variants_inline",
    }
    for s in m["strategies"]:
        bundle = _load(f"strategies/{s['strategy_id']}.json")
        missing = required - bundle.keys()
        assert not missing, f"{s['strategy_id']} missing: {missing}"


def test_aggregate_metrics_use_5num_summary():
    m = _load("manifest.json")
    sample = next(s for s in m["strategies"] if s["variant_count"] >= 4)
    bundle = _load(f"strategies/{sample['strategy_id']}.json")
    agg = bundle["aggregate_metrics"]
    assert "wf_sharpe" in agg
    summary = agg["wf_sharpe"]
    for key in ("min", "p25", "median", "p75", "max", "best_hash"):
        assert key in summary, f"wf_sharpe missing {key}"


def test_headline_variant_in_inline_variants():
    m = _load("manifest.json")
    for s in m["strategies"]:
        bundle = _load(f"strategies/{s['strategy_id']}.json")
        hashes = {v["params_hash"] for v in bundle["variants_inline"]}
        assert bundle["headline_variant_hash"] in hashes
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_build_data_strategy_bundles.py -v`
Expected: `KeyError: 'hash_to_strategy_id'` on the manifest test (current manifest doesn't have it). Other tests will likely fail too.

- [ ] **Step 3: Refactor `build_data.py`**

In `scripts/build_data.py`, after the existing imports, add:

```python
import bursahack.signals  # noqa: F401 — populates Strategy.__subclasses__()
from bursahack.signals.registry import (
    all_strategies,
    group_variants_by_strategy,
    strategy_id_for,
)
```

After the existing helper functions, add these new functions:

```python
def _five_num_summary(values: list[tuple[float, str]]) -> dict:
    """Five-number summary + best_hash for a list of (value, params_hash)."""
    vals = sorted([v for v, _ in values if v is not None])
    by_hash = sorted(values, key=lambda t: (t[0] if t[0] is not None else -1e18))
    if not vals:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None, "best_hash": None}

    def q(p: float) -> float:
        idx = int(round(p * (len(vals) - 1)))
        return float(vals[idx])

    return {
        "min": float(vals[0]),
        "p25": q(0.25),
        "median": q(0.50),
        "p75": q(0.75),
        "max": float(vals[-1]),
        "best_hash": by_hash[-1][1],
    }


def _pick_headline(variants: list[dict], rule: str) -> tuple[str, str]:
    """Return (params_hash, reason) of the headline variant per HEADLINE_RULE.

    Only one rule shape is supported today: "max wf_sharpe" plus optional
    constraints "s.t. cov<=X, slip_drag<=Y" (comma-separated). Anything more
    exotic falls back to plain "max wf_sharpe".
    """
    base_rule = rule.strip()
    constraints = []
    if "s.t." in base_rule:
        head, tail = base_rule.split("s.t.", 1)
        base_rule = head.strip()
        for clause in tail.split(","):
            clause = clause.strip()
            for op in ("<=", ">=", "<", ">", "="):
                if op in clause:
                    key, val = clause.split(op, 1)
                    constraints.append((key.strip(), op, float(val.strip())))
                    break

    candidates = list(variants)
    for key, op, val in constraints:
        if op == "<=":
            candidates = [v for v in candidates if v.get(key) is not None and v[key] <= val]
        elif op == ">=":
            candidates = [v for v in candidates if v.get(key) is not None and v[key] >= val]
        elif op == "<":
            candidates = [v for v in candidates if v.get(key) is not None and v[key] < val]
        elif op == ">":
            candidates = [v for v in candidates if v.get(key) is not None and v[key] > val]
        elif op == "=":
            candidates = [v for v in candidates if v.get(key) == val]
    if not candidates:
        candidates = list(variants)  # fall back to unconstrained

    metric_key = base_rule.replace("max ", "").strip() or "wf_sharpe"
    best = max(candidates, key=lambda v: v.get(metric_key) or -1e18)
    return best["params_hash"], rule


def _tier_rank(t: str | None) -> int:
    return {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}.get(t or "F", 6)


def build_strategy_bank(
    final_scorecards: list[dict],
    summary_rows: list[dict],
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Build per-strategy bundles + manifest entries + hash->id lookup.

    Returns (bundle_list, manifest_entries, hash_to_strategy_id).
    """
    sc_by_hash = {r["params_hash"]: r for r in final_scorecards}
    # Decorate summary rows with parsed params
    rows: list[dict] = []
    for r in summary_rows:
        try:
            params = json.loads(r.get("params") or "{}")
        except json.JSONDecodeError:
            params = {}
        rows.append({**r, "params": params})

    shape_keys_for: dict[str, tuple[str, ...]] = {}
    family_class: dict[str, type] = {}
    for cls in all_strategies():
        # name attribute is the family slug on each Strategy subclass
        inst = cls.__new__(cls)
        family = getattr(cls, "name", None) or cls.__dataclass_fields__["name"].default
        family_class[family] = cls
        shape_keys_for[family] = cls.SHAPE_KEYS

    grouped = group_variants_by_strategy(rows, shape_keys_for)

    bundles: list[dict] = []
    manifest_entries: list[dict] = []
    hash_to_sid: dict[str, str] = {}

    for sid, variants in grouped.items():
        family = variants[0]["strategy"]
        cls = family_class[family]
        shape = {k: variants[0]["params"][k] for k in cls.SHAPE_KEYS}

        # Decorate variants with the union of summary + scorecard fields we want
        inline_variants: list[dict] = []
        agg_inputs: dict[str, list[tuple[float, str]]] = {
            "wf_sharpe": [], "oos_sharpe": [], "max_dd": [], "cov": [],
            "cagr_oos": [], "slip_drag": [], "order_mult": [],
        }
        for r in variants:
            h = r["params_hash"]
            hash_to_sid[h] = sid
            sc = sc_by_hash.get(h, {})

            def _num(d: dict, k: str) -> float | None:
                v = d.get(k)
                if v is None or v == "":
                    return None
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return None

            v_row = {
                "params_hash": h,
                "params": r["params"],
                "wf_sharpe":  _num(sc, "wf_sharpe") if sc else _num(r, "sharpe_mean"),
                "oos_sharpe": _num(sc, "oos_sharpe"),
                "cov":        _num(sc, "cov"),
                "max_dd":     _num(sc, "max_dd"),
                "cagr_oos":   _num(sc, "cagr_oos"),
                "slip_drag":  _num(sc, "slip_drag"),
                "order_mult": _num(sc, "order_mult"),
                "monthly_hit": _num(sc, "monthly_hit"),
                "tier":       sc.get("tier") if sc else None,
                "n_pass":     int(sc["n_pass"]) if sc.get("n_pass") else None,
                "n_eval":     int(sc["n_eval"]) if sc.get("n_eval") else None,
            }
            inline_variants.append(v_row)
            for metric in agg_inputs:
                val = v_row.get(metric)
                if val is not None:
                    agg_inputs[metric].append((val, h))

        head_hash, head_reason = _pick_headline(inline_variants, cls.HEADLINE_RULE)
        for v in inline_variants:
            v["headline"] = (v["params_hash"] == head_hash)

        agg_metrics = {k: _five_num_summary(v) for k, v in agg_inputs.items()}

        # display_name: append shape tokens in human form
        shape_human = []
        for k in cls.SHAPE_KEYS:
            val = shape[k]
            if isinstance(val, bool):
                shape_human.append("regime on" if (k == "use_regime" and val) else "regime off" if k == "use_regime" else f"{k}={val}")
            elif k == "rebal_freq":
                shape_human.append({"W": "weekly", "M": "monthly", "Q": "quarterly", "2W": "biweekly"}.get(str(val), str(val)))
            else:
                shape_human.append(f"{k}={val}")
        display_name = cls.DISPLAY_NAME + (f" — {', '.join(shape_human)}" if shape_human else "")

        # Auto-generated one-liner: ranges of CONT_KEYS + headline values
        cont_ranges_bits = []
        head_variant = next(v for v in inline_variants if v["headline"])
        head_value_bits = []
        for ck in cls.CONT_KEYS:
            vals = sorted({v["params"].get(ck) for v in inline_variants if v["params"].get(ck) is not None})
            if not vals:
                continue
            if len(vals) == 1:
                cont_ranges_bits.append(f"{ck}={vals[0]}")
            else:
                cont_ranges_bits.append(f"{ck}={vals[0]}-{vals[-1]}")
            hv = head_variant["params"].get(ck)
            if hv is not None:
                head_value_bits.append(f"{ck}={hv}")
        one_liner = f"{len(inline_variants)} variants. " + ", ".join(cont_ranges_bits)
        if head_value_bits:
            one_liner += ". Best: " + ", ".join(head_value_bits)

        bundle = {
            "strategy_id": sid,
            "family": family,
            "display_name": display_name,
            "shape": shape,
            "short_blurb": cls.SHORT_BLURB,
            "definition_md": cls.DEFINITION_MD,
            "references": list(cls.REFERENCES),
            "source_file": cls.SOURCE_FILE,
            "added": cls.ADDED,
            "one_liner": one_liner,
            "variant_count": len(inline_variants),
            "headline_variant_hash": head_hash,
            "headline_reason": head_reason,
            "aggregate_metrics": agg_metrics,
            "variants_inline": inline_variants,
        }
        bundles.append(bundle)

        # Manifest summary
        best_oos_summary = agg_metrics["oos_sharpe"]
        best_tier = min(
            (v["tier"] for v in inline_variants if v["tier"]),
            key=_tier_rank,
            default=None,
        )
        head_v = next(v for v in inline_variants if v["headline"])
        manifest_entries.append({
            "strategy_id": sid,
            "family": family,
            "display_name": display_name,
            "best_oos_sharpe": best_oos_summary.get("max"),
            "best_tier": best_tier,
            "variant_count": len(inline_variants),
            "headline_variant_hash": head_hash,
            "headline_gates_passed": head_v.get("n_pass"),
            "headline_gates_evaluated": head_v.get("n_eval"),
        })

    # Sort manifest entries by best_oos_sharpe desc (NaN/None last)
    manifest_entries.sort(key=lambda m: m["best_oos_sharpe"] if m["best_oos_sharpe"] is not None else -1e18, reverse=True)

    return bundles, manifest_entries, hash_to_sid
```

Then in `main()`, **replace** the existing `rotation_bundle` + `clenow_bundle` build + their per-strategy file writes with:

```python
    # NEW: bank-style per-strategy bundles
    bundles, manifest_strategy_entries, hash_to_sid = build_strategy_bank(
        final_scorecards, summary_rows,
    )

    # Write each bundle to web/data/strategies/<strategy_id>.json
    for bundle in bundles:
        path = OUT / "strategies" / f"{bundle['strategy_id']}.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        # Also write per-variant equity to web/data/equity/ for the slide-over.
        # NOTE: only the *headline* variant equity exists today (rotation +
        # clenow); other variants will fall through to "no equity" on the FE
        # until a future task generates them. That's fine — the slide-over
        # gracefully handles missing equity.

    # Strategy aliases (legacy URL redirects)
    aliases = {
        "rotation_rank_1":   "rotation__rebal-M",
        "clenow_som_rank_9": "clenow_som__regime-on__rebal-M",
    }
    (OUT / "strategy_aliases.json").write_text(json.dumps(aliases, indent=2), encoding="utf-8")
```

In the `manifest = {...}` construction, **replace** the existing `"strategies": [...]` block with:

```python
        "strategies": manifest_strategy_entries,
        "hash_to_strategy_id": hash_to_sid,
```

Also keep the existing `rotation_bundle` and `clenow_bundle` writes for backward compat (the legacy `web/data/strategies/rotation_rank_1.json` + `clenow_som_rank_9.json` files) so old URLs work until the alias-based FE redirects ship in Task 8.

- [ ] **Step 4: Run tests, verify pass**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python -m pytest tests/test_build_data_strategy_bundles.py -v`
Expected: `6 passed` (or 7 if module-fixture passes cleanly).

- [ ] **Step 5: Run pipeline end-to-end + sanity-check the output count**

Run: `cd C:/Users/Workstation/Desktop/BursaHack && .venv/Scripts/python scripts/build_data.py`

Then:

```bash
cd C:/Users/Workstation/Desktop/BursaHack
ls web/data/strategies/ | wc -l        # expect roughly 12-18 NEW *.json files + 2 legacy
cat web/data/manifest.json | python -c "import json,sys; m=json.load(sys.stdin); print('strategies:', len(m['strategies'])); print('hashes:', len(m['hash_to_strategy_id']))"
cat web/data/strategy_aliases.json
```

Expected: ~12-20 strategies, ~80-200 hashes, alias file shows the two legacy redirects.

- [ ] **Step 6: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add scripts/build_data.py tests/test_build_data_strategy_bundles.py web/data/strategies/ web/data/strategy_aliases.json web/data/manifest.json
git commit -m "feat(build_data): auto-derive per-strategy bundles + alias table

Walks Strategy.__subclasses__(), groups search_summary rows by
(family, shape_tuple), emits one bundle at web/data/strategies/<sid>.json
per strategy_id. ~12-18 bundles vs the prior 2 hand-coded.

Adds web/data/strategy_aliases.json (legacy URL -> current sid) and
hash_to_strategy_id lookup in manifest (drives /search/<hash> redirect).

build_strategy_bank() handles 5-num summaries (mean intentionally absent),
HEADLINE_RULE parsing (\"max wf_sharpe s.t. cov<=X, slip_drag<=Y\"), and
auto-generates a human one-liner from CONT_KEYS ranges + headline values.

Legacy rotation_rank_1 + clenow_som_rank_9 bundle writes are preserved
until the FE alias redirects ship (Task 8). 7/7 new pipeline tests pass."
```

---

### Task 6: Verify bundle shape matches FE types end-to-end (no-code sanity gate)

**Files:** (no file changes)

- [ ] **Step 1: Manual spot-check of one bundle**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
python -c "
import json, sys
m = json.load(open('web/data/manifest.json'))
sample = m['strategies'][0]
print('top strategy:', sample['strategy_id'])
print('best_oos_sharpe:', sample['best_oos_sharpe'])
b = json.load(open(f'web/data/strategies/{sample[\"strategy_id\"]}.json'))
print('headline_variant:', b['headline_variant_hash'])
print('variant_count:', b['variant_count'])
print('one_liner:', b['one_liner'])
print('aggregate_metrics.wf_sharpe:', b['aggregate_metrics']['wf_sharpe'])
"
```

Expected: prints non-null values; one_liner reads like a sentence.

- [ ] **Step 2: Verify hash_to_strategy_id covers existing variant pages**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
python -c "
import json, os
m = json.load(open('web/data/manifest.json'))
prerendered = {f.replace('.json','') for f in os.listdir('web/data/variants') if f.endswith('.json')}
mapped = set(m['hash_to_strategy_id'])
missing = prerendered - mapped
print(f'prerendered variants: {len(prerendered)}')
print(f'mapped in manifest:   {len(mapped)}')
print(f'unmapped:             {len(missing)}')
if missing:
    print('sample unmapped:', list(missing)[:5])
"
```

Expected: `unmapped: 0`. If non-zero, those variants will 404 on the redirect — debug `build_strategy_bank` group inputs before moving on.

- [ ] **Step 3: Commit nothing if all good**

This is a sanity gate, not a code task. No commit needed if both checks pass.

---

## Phase 3 — Strategy bank index route (Task 7)

### Task 7: Add `/strategies` bank index page + table

**Files:**
- Create: `web/src/app/strategies/page.tsx`
- Create: `web/src/components/strategy/bank-table.tsx`
- Modify: `web/src/lib/types.ts` — add new types
- Modify: `web/src/lib/data.ts` — `getBankIndex()` loader (read it first if unsure of pattern)
- Test: `web/tests/e2e/strategy-bank.spec.ts` (new)

- [ ] **Step 1: Extend types.ts**

Add to `web/src/lib/types.ts`:

```typescript
export interface BankIndexEntry {
  strategy_id: string;
  family: string;
  display_name: string;
  best_oos_sharpe: number | null;
  best_tier: string | null;
  variant_count: number;
  headline_variant_hash: string;
  headline_gates_passed: number | null;
  headline_gates_evaluated: number | null;
}

export interface ManifestExtended extends Manifest {
  hash_to_strategy_id: Record<string, string>;
}

export interface FiveNumSummary {
  min: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  max: number | null;
  best_hash: string | null;
}

export interface VariantInline {
  params_hash: string;
  params: Record<string, unknown>;
  wf_sharpe: number | null;
  oos_sharpe: number | null;
  cov: number | null;
  max_dd: number | null;
  cagr_oos: number | null;
  slip_drag: number | null;
  order_mult: number | null;
  monthly_hit: number | null;
  tier: string | null;
  n_pass: number | null;
  n_eval: number | null;
  headline: boolean;
}

export interface StrategyBundleV2 {
  strategy_id: string;
  family: string;
  display_name: string;
  shape: Record<string, unknown>;
  short_blurb: string;
  definition_md: string;
  references: Array<{ title: string; author?: string; year?: number }>;
  source_file: string;
  added: string;
  one_liner: string;
  variant_count: number;
  headline_variant_hash: string;
  headline_reason: string;
  aggregate_metrics: Record<string, FiveNumSummary>;
  variants_inline: VariantInline[];
}
```

Update `ManifestStrategy` to include the new fields if it didn't already:

```typescript
export interface ManifestStrategy {
  slug?: string;  // legacy
  strategy_id?: string;  // new
  family: string;
  label?: string;  // legacy
  display_name?: string;  // new
  tier?: string | null;
  best_tier?: string | null;
  oos_sharpe?: number | null;
  best_oos_sharpe?: number | null;
  variant_count?: number;
  headline_variant_hash?: string;
  headline_gates_passed?: number | null;
  headline_gates_evaluated?: number | null;
}
```

- [ ] **Step 2: Extend data.ts**

Read `web/src/lib/data.ts` to learn the existing helper pattern (`getManifest`, `getStrategy`, etc.), then add:

```typescript
import type { StrategyBundleV2 } from "./types";

export async function getBankIndex() {
  const m = await getManifest();
  return m.strategies; // already sorted desc by best_oos_sharpe in build_data.py
}

export async function getStrategyV2(strategyId: string): Promise<StrategyBundleV2> {
  const path = `${DATA_DIR}/strategies/${strategyId}.json`;
  // Use whatever fs.read / fetch pattern existing functions use; match exactly.
  const raw = await fs.readFile(path, "utf-8");
  return JSON.parse(raw) as StrategyBundleV2;
}

export async function getStrategyAliases(): Promise<Record<string, string>> {
  try {
    const raw = await fs.readFile(`${DATA_DIR}/strategy_aliases.json`, "utf-8");
    return JSON.parse(raw) as Record<string, string>;
  } catch {
    return {};
  }
}
```

(Adapt `fs.readFile` / `DATA_DIR` to whatever the existing `data.ts` uses — match the existing pattern exactly.)

- [ ] **Step 3: Create the bank-table component**

Create `web/src/components/strategy/bank-table.tsx`:

```tsx
"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { ManifestStrategy } from "@/lib/types";

type SortKey = "best_oos_sharpe" | "variant_count" | "best_tier" | "headline_gates_passed";
type Dir = "asc" | "desc";

const COLS: Array<{ key: SortKey | "display_name"; label: string; align?: "right" }> = [
  { key: "display_name", label: "Strategy" },
  { key: "variant_count", label: "Variants", align: "right" },
  { key: "best_oos_sharpe", label: "Best OOS Sharpe", align: "right" },
  { key: "best_tier", label: "Best tier", align: "right" },
  { key: "headline_gates_passed", label: "Gates ✓", align: "right" },
];

const TIER_RANK: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4, F: 5 };

export function BankTable({ entries }: { entries: ManifestStrategy[] }) {
  const [sort, setSort] = React.useState<{ key: SortKey; dir: Dir }>({
    key: "best_oos_sharpe", dir: "desc",
  });
  const [familyFilter, setFamilyFilter] = React.useState<string | null>(null);

  const families = Array.from(new Set(entries.map((e) => e.family))).sort();

  const filtered = familyFilter ? entries.filter((e) => e.family === familyFilter) : entries;

  const sorted = React.useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const k = sort.key;
      let av: number = 0, bv: number = 0;
      if (k === "best_tier") {
        av = TIER_RANK[a.best_tier ?? "F"] ?? 99;
        bv = TIER_RANK[b.best_tier ?? "F"] ?? 99;
      } else {
        av = (a[k] as number | null) ?? -Infinity;
        bv = (b[k] as number | null) ?? -Infinity;
      }
      const cmp = av > bv ? 1 : av < bv ? -1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sort]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">Family:</span>
        <button
          type="button"
          onClick={() => setFamilyFilter(null)}
          className={`px-2 py-0.5 rounded border ${familyFilter === null ? "border-foreground bg-accent" : "border-border hover:bg-muted"}`}
        >
          All
        </button>
        {families.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFamilyFilter(f)}
            className={`px-2 py-0.5 rounded border ${familyFilter === f ? "border-foreground bg-accent" : "border-border hover:bg-muted"}`}
          >
            {f}
          </button>
        ))}
      </div>
      <div className="rounded-md border border-border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {COLS.map((c) => (
                <TableHead key={c.key} className={c.align === "right" ? "text-right" : undefined}>
                  {c.key === "display_name" ? c.label : (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() => setSort((s) => ({
                        key: c.key as SortKey,
                        dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc",
                      }))}
                    >
                      {c.label}
                      {sort.key === c.key ? (
                        sort.dir === "asc"
                          ? <ArrowUp className="size-3" aria-hidden />
                          : <ArrowDown className="size-3" aria-hidden />
                      ) : <ArrowUpDown className="size-3 opacity-30" aria-hidden />}
                    </button>
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((e) => (
              <TableRow key={e.strategy_id}>
                <TableCell>
                  <Link
                    href={`/strategies/${e.strategy_id}/`}
                    className="hover:underline font-medium"
                  >
                    {e.display_name}
                  </Link>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{e.family}</div>
                </TableCell>
                <TableCell className="text-right tabular">{e.variant_count ?? "—"}</TableCell>
                <TableCell className="text-right tabular">
                  {e.best_oos_sharpe != null ? e.best_oos_sharpe.toFixed(2) : "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="outline">{e.best_tier ?? "—"}</Badge>
                </TableCell>
                <TableCell className="text-right tabular">
                  {e.headline_gates_passed ?? "—"} / {e.headline_gates_evaluated ?? "—"}
                </TableCell>
              </TableRow>
            ))}
            {!sorted.length && (
              <TableRow>
                <TableCell colSpan={COLS.length} className="text-center py-6 text-muted-foreground">
                  No strategies match.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create the page**

Create `web/src/app/strategies/page.tsx`:

```tsx
import { getManifest } from "@/lib/data";
import { BankTable } from "@/components/strategy/bank-table";

export const metadata = {
  title: "Strategy Bank",
  description: "All strategy concepts in the BursaHack research, grouped by family x broad-shape switches.",
};

export default async function StrategiesIndex() {
  const manifest = await getManifest();
  const strategies = manifest.strategies;

  const totalVariants = strategies.reduce((s, e) => s + ((e as any).variant_count ?? 0), 0);
  const tiers = new Set(strategies.map((s) => (s as any).best_tier).filter(Boolean));

  return (
    <div className="space-y-6 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Strategy Bank
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">Strategies in the BursaHack research</h1>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
          {strategies.length} strategies, {totalVariants} parameter variants searched. Each row is one strategy concept; click in to see how the variants under it behaved.
        </p>
      </header>
      <BankTable entries={strategies as any} />
    </div>
  );
}
```

- [ ] **Step 5: Add Playwright smoke for the new route**

Create `web/tests/e2e/strategy-bank.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";

test("bank index page loads and lists strategies", async ({ page }) => {
  await page.goto("/strategies/");
  await expect(page.locator("h1")).toContainText("Strategies in the BursaHack research");
  // Expect at least one row (any tier letter cell)
  const tierBadges = page.locator('[data-slot="badge"], [role="status"]').filter({ hasText: /^[ABCDF]$/ });
  await expect(tierBadges.first()).toBeVisible();
});

test("bank index row links to a strategy detail page", async ({ page }) => {
  await page.goto("/strategies/");
  const firstLink = page.locator("table a").first();
  const href = await firstLink.getAttribute("href");
  expect(href).toMatch(/^\/strategies\/.+\/?$/);
  await firstLink.click();
  await page.waitForLoadState("domcontentloaded");
  // Detail page must have an h1
  await expect(page.locator("h1").first()).toBeVisible();
});
```

- [ ] **Step 6: Build + run smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -10
```

Expected: build succeeds. New `/strategies` route appears in the routes table.

Then:

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm exec playwright test strategy-bank --project=desktop --reporter=line
```

Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/app/strategies/page.tsx web/src/components/strategy/bank-table.tsx web/src/lib/types.ts web/src/lib/data.ts web/tests/e2e/strategy-bank.spec.ts
git commit -m "feat(portal): /strategies bank index route

New page lists every strategy in the bank (12-18 entries), one row per
strategy_id with family chip, variant count, best OOS Sharpe, best tier,
headline-variant gates. Sortable, family-filter chip row.

Detail page link goes to /strategies/<strategy_id>/ (refactor in next task).

Smoke 2/2 (Playwright)."
```

---

## Phase 4 — Strategy detail page + variant explorer (Tasks 8-11)

### Task 8: Refactor `/strategies/[slug]/page.tsx` for bank bundles + alias redirects

**Files:**
- Modify: `web/src/app/strategies/[slug]/page.tsx`

- [ ] **Step 1: Read the current file to understand what's there**

Read `web/src/app/strategies/[slug]/page.tsx` (existing per-variant detail page). Keep the existing tabs structure (Performance / Scorecard / Methodology / Costs / Trades / Risks); we extend rather than rewrite.

- [ ] **Step 2: Add alias resolution + bundle loading at the top**

Replace the top of the file with:

```tsx
import { notFound, redirect } from "next/navigation";

import {
  getStrategy, getStrategyV2, getStrategyAliases, getManifest, getEquity, getFolds, getTrades,
} from "@/lib/data";

// ... existing component imports unchanged ...

export async function generateStaticParams() {
  const manifest = await getManifest();
  const newIds = manifest.strategies.map((s: any) => ({ slug: s.strategy_id ?? s.slug }));
  // Legacy slugs also need to be prerendered so the alias redirect works
  // without a server runtime.
  return [
    ...newIds,
    { slug: "rotation_rank_1" },
    { slug: "clenow_som_rank_9" },
  ];
}

interface PageProps {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}

export default async function StrategyPage({ params, searchParams }: PageProps) {
  const { slug } = await params;
  const sp = await searchParams;

  // 1) Alias redirect: /strategies/rotation_rank_1 -> /strategies/rotation__rebal-M
  const aliases = await getStrategyAliases();
  if (aliases[slug]) {
    redirect(`/strategies/${aliases[slug]}/`);
  }

  // 2) Try the new bank bundle first
  let bundle: Awaited<ReturnType<typeof getStrategyV2>> | null = null;
  try {
    bundle = await getStrategyV2(slug);
  } catch {
    // Fall back to legacy bundle for backward compat
  }

  if (bundle) {
    return <StrategyBankPage bundle={bundle} searchParams={sp} />;
  }

  // Legacy path (will go away after all callers move to new ids)
  try {
    const strategy = await getStrategy(slug);
    return <StrategyLegacyPage strategy={strategy} searchParams={sp} />;
  } catch {
    notFound();
  }
}
```

- [ ] **Step 3: Add the new `StrategyBankPage` component (same file, below the default export)**

```tsx
async function StrategyBankPage({ bundle, searchParams }: {
  bundle: Awaited<ReturnType<typeof getStrategyV2>>;
  searchParams: Record<string, string | undefined>;
}) {
  const head = bundle.variants_inline.find((v) => v.headline) ?? bundle.variants_inline[0];
  const selectedHash = searchParams.v ?? null;

  return (
    <div className="space-y-8 fade-rise">
      <header className="space-y-2 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-brand-gold-700)]">
          Strategy Bank
        </p>
        <h1 className="font-heading text-3xl sm:text-4xl">{bundle.display_name}</h1>
        <p className="text-sm text-muted-foreground">{bundle.short_blurb}</p>
        <p className="text-xs text-muted-foreground tabular">{bundle.one_liner}</p>
      </header>

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <Kpi label="Variants" value={String(bundle.variant_count)} />
        <Kpi label="Best OOS Sharpe" value={fmtNum(bundle.aggregate_metrics.oos_sharpe?.max)} />
        <Kpi label="Median WF Sharpe" value={fmtNum(bundle.aggregate_metrics.wf_sharpe?.median)} />
        <Kpi label="Headline tier" value={head.tier ?? "—"} />
      </section>

      {/* Variant explorer — table + heatmap. Components added in Tasks 9-10. */}
      <VariantExplorer bundle={bundle} selectedHash={selectedHash} />

      {/* Definition (existing MDX render pattern) */}
      <section className="prose prose-sm max-w-none">
        {/* Render bundle.definition_md as markdown via the existing helper */}
        <pre className="whitespace-pre-wrap">{bundle.definition_md}</pre>
      </section>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="font-heading text-xl tabular">{value}</div>
    </div>
  );
}

function fmtNum(v: number | null | undefined) {
  return v == null ? "—" : v.toFixed(2);
}
```

The existing `StrategyPage` content moves under a new `StrategyLegacyPage` wrapper — copy-paste the existing rendered JSX into a function with that name, keeping the legacy paths working.

- [ ] **Step 4: Build to verify it compiles (no smoke yet — needs Tasks 9-11)**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -10
```

Expected: build succeeds. `VariantExplorer` import will fail — temporarily stub it inline as `function VariantExplorer() { return null; }` for this commit. The real component lands in Task 9.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/app/strategies/[slug]/page.tsx
git commit -m "feat(portal): /strategies/<id> reads new bank bundle + alias redirect

The strategy detail page now:
- redirects legacy slugs (rotation_rank_1, clenow_som_rank_9) via the
  alias table to their new strategy_ids
- prefers the new bank bundle (web/data/strategies/<sid>.json) and
  falls back to the legacy hand-coded bundles
- renders the headline summary + 4 KPI tiles (variants, best OOS,
  median WF, headline tier)

VariantExplorer is stubbed for now; real component lands in Task 9.
generateStaticParams includes both new ids and legacy slugs so the
alias redirect works without a server runtime."
```

---

### Task 9: `VariantExplorer` — sortable scorecard table with dominance filter

**Files:**
- Create: `web/src/components/strategy/variant-explorer.tsx`
- Create: `web/src/components/strategy/dominance.ts`
- Modify: `web/src/app/strategies/[slug]/page.tsx` (swap stub for real import)

- [ ] **Step 1: Implement the dominance utility**

Create `web/src/components/strategy/dominance.ts`:

```typescript
import type { VariantInline } from "@/lib/types";

// Frozen dominance gate set per spec §6.1.
const GATES: Array<{
  key: keyof VariantInline;
  better: "higher" | "lower";
}> = [
  { key: "oos_sharpe", better: "higher" },
  { key: "max_dd",     better: "higher" },   // less negative is better
  { key: "cov",        better: "lower" },
  // DSR_eff is not in VariantInline today; add when available.
];

function leq(a: number, b: number, better: "higher" | "lower"): boolean {
  return better === "higher" ? a <= b : a >= b;
}

function lt(a: number, b: number, better: "higher" | "lower"): boolean {
  return better === "higher" ? a < b : a > b;
}

export function isDominated(
  candidate: VariantInline,
  others: VariantInline[],
): boolean {
  for (const other of others) {
    if (other.params_hash === candidate.params_hash) continue;
    let allBetterOrEqual = true;
    let strictlyBetterOnOne = false;
    for (const g of GATES) {
      const a = candidate[g.key] as number | null;
      const b = other[g.key] as number | null;
      if (a == null || b == null) { allBetterOrEqual = false; break; }
      if (!leq(a, b, g.better)) { allBetterOrEqual = false; break; }
      if (lt(a, b, g.better)) strictlyBetterOnOne = true;
    }
    if (allBetterOrEqual && strictlyBetterOnOne) return true;
  }
  return false;
}

export function nonDominated(variants: VariantInline[]): VariantInline[] {
  return variants.filter((v) => !isDominated(v, variants));
}
```

- [ ] **Step 2: Implement `VariantExplorer`**

Create `web/src/components/strategy/variant-explorer.tsx`:

```tsx
"use client";

import * as React from "react";
import { useRouter, usePathname } from "next/navigation";
import { ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";

import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { nonDominated } from "./dominance";
import type { StrategyBundleV2, VariantInline } from "@/lib/types";

type NumKey =
  | "wf_sharpe" | "oos_sharpe" | "cov" | "max_dd"
  | "cagr_oos" | "slip_drag" | "order_mult" | "n_pass";

const COLS: Array<{ key: NumKey | "params" | "tier"; label: string; align?: "right" }> = [
  { key: "params", label: "Params" },
  { key: "wf_sharpe",  label: "WF Sharpe",  align: "right" },
  { key: "oos_sharpe", label: "OOS Sharpe", align: "right" },
  { key: "cov",        label: "fold CoV",   align: "right" },
  { key: "max_dd",     label: "Max DD",     align: "right" },
  { key: "cagr_oos",   label: "CAGR OOS",   align: "right" },
  { key: "slip_drag",  label: "Drag",       align: "right" },
  { key: "order_mult", label: "Order x",    align: "right" },
  { key: "n_pass",     label: "Gates ✓",   align: "right" },
  { key: "tier",       label: "Tier",       align: "right" },
];

const TIER_RANK: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4, F: 5 };

export function VariantExplorer({
  bundle,
  selectedHash,
}: {
  bundle: StrategyBundleV2;
  selectedHash: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [sort, setSort] = React.useState<{ key: NumKey | "tier"; dir: "asc" | "desc" }>({
    key: "wf_sharpe", dir: "desc",
  });
  const [hideDominated, setHideDominated] = React.useState(false);

  const rows = React.useMemo(() => {
    const base = hideDominated ? nonDominated(bundle.variants_inline) : bundle.variants_inline;
    const arr = [...base];
    arr.sort((a, b) => {
      let av: number = 0, bv: number = 0;
      if (sort.key === "tier") {
        av = TIER_RANK[a.tier ?? "F"] ?? 99;
        bv = TIER_RANK[b.tier ?? "F"] ?? 99;
      } else {
        av = (a[sort.key] as number | null) ?? -Infinity;
        bv = (b[sort.key] as number | null) ?? -Infinity;
      }
      const cmp = av > bv ? 1 : av < bv ? -1 : 0;
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [bundle, sort, hideDominated]);

  const openVariant = (hash: string) => {
    router.push(`${pathname}?v=${hash}`);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-heading text-lg">Variants ({bundle.variant_count})</h2>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={hideDominated}
            onChange={(e) => setHideDominated(e.target.checked)}
          />
          Hide dominated (OOS Sharpe / Max DD / fold CoV)
        </label>
      </div>
      <div className="rounded-md border border-border overflow-x-auto">
        <Table className="min-w-[820px]">
          <TableHeader>
            <TableRow>
              {COLS.map((c) => (
                <TableHead key={c.key} className={c.align === "right" ? "text-right" : undefined}>
                  {c.key === "params" ? c.label : (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() => setSort((s) => ({
                        key: c.key as NumKey | "tier",
                        dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc",
                      }))}
                    >
                      {c.label}
                      {sort.key === c.key
                        ? (sort.dir === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)
                        : <ArrowUpDown className="size-3 opacity-30" />}
                    </button>
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((v) => (
              <TableRow
                key={v.params_hash}
                className={`cursor-pointer ${v.params_hash === selectedHash ? "bg-accent" : "hover:bg-muted"} ${v.headline ? "border-l-4 border-l-[var(--color-brand-gold)]" : ""}`}
                onClick={() => openVariant(v.params_hash)}
              >
                <TableCell className="font-mono text-[10px]">
                  {Object.entries(v.params).slice(0, 4).map(([k, val]) => (
                    <span key={k} className="mr-2">{k}={String(val)}</span>
                  ))}
                </TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.wf_sharpe)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.oos_sharpe)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.cov)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmtPct(v.max_dd)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmtPct(v.cagr_oos)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmtPct(v.slip_drag)}</TableCell>
                <TableCell className="text-right tabular text-xs">{fmt(v.order_mult)}</TableCell>
                <TableCell className="text-right tabular text-xs">
                  {v.n_pass ?? "—"} / {v.n_eval ?? "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="outline">{v.tier ?? "—"}</Badge>
                </TableCell>
              </TableRow>
            ))}
            {!rows.length && (
              <TableRow>
                <TableCell colSpan={COLS.length} className="text-center py-6 text-muted-foreground">
                  No variants after filter.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function fmt(v: number | null | undefined) {
  return v == null ? "—" : v.toFixed(2);
}
function fmtPct(v: number | null | undefined) {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}
```

- [ ] **Step 3: Wire the import in `page.tsx`**

In `web/src/app/strategies/[slug]/page.tsx`, replace the stub:

```tsx
function VariantExplorer() { return null; }
```

with:

```tsx
import { VariantExplorer } from "@/components/strategy/variant-explorer";
```

- [ ] **Step 4: Build + smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
pnpm exec playwright test smoke --project=desktop --reporter=line 2>&1 | tail -10
```

Expected: build green, smoke 11/11 (still pre-Plotly-heatmap).

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/components/strategy/variant-explorer.tsx web/src/components/strategy/dominance.ts web/src/app/strategies/[slug]/page.tsx
git commit -m "feat(portal): VariantExplorer table on strategy bank page

Sortable scorecard table for the N variants under one strategy. Frozen
dominance set (OOS Sharpe / MaxDD / fold-CoV per spec §6.1) drives the
\"Hide dominated\" toggle. Headline variant marked with gold left border.
Click a row -> ?v=<hash> in URL (slide-over comes in Task 11).

11/11 smoke green."
```

---

### Task 10: Parameter heatmap on strategy page

**Files:**
- Create: `web/src/components/strategy/param-heatmap.tsx`
- Modify: `web/src/app/strategies/[slug]/page.tsx` — mount it next to the table

- [ ] **Step 1: Implement the heatmap component**

Create `web/src/components/strategy/param-heatmap.tsx`:

```tsx
"use client";

import * as React from "react";
import { useRouter, usePathname } from "next/navigation";

import { PlotlyChart } from "../charts/plotly-chart";
import { PALETTE } from "@/lib/theme";
import type { VariantInline } from "@/lib/types";

interface Props {
  variants: VariantInline[];
  contKeys: string[];  // candidate axes (CONT_KEYS from the family)
}

export function ParamHeatmap({ variants, contKeys }: Props) {
  // Only consider keys that actually vary across this strategy's variants
  const varyingKeys = React.useMemo(() => {
    return contKeys.filter((k) => {
      const values = new Set(variants.map((v) => v.params[k]));
      return values.size >= 2;
    });
  }, [variants, contKeys]);

  const [xKey, setXKey] = React.useState<string>(varyingKeys[0] ?? "");
  const [yKey, setYKey] = React.useState<string>(varyingKeys[1] ?? varyingKeys[0] ?? "");
  const router = useRouter();
  const pathname = usePathname();

  if (varyingKeys.length < 2) {
    return (
      <div className="rounded-md border border-border p-4 text-xs text-muted-foreground">
        Heatmap needs at least two continuous parameters to vary; this strategy doesn't have that. Use the table.
      </div>
    );
  }

  const xVals = Array.from(new Set(variants.map((v) => v.params[xKey]))).sort(numOrStr);
  const yVals = Array.from(new Set(variants.map((v) => v.params[yKey]))).sort(numOrStr);

  // Cell = best variant per (x, y) by wf_sharpe + +N badge if >1
  const cellMap = new Map<string, { best: VariantInline; count: number }>();
  for (const v of variants) {
    const xv = v.params[xKey];
    const yv = v.params[yKey];
    if (xv == null || yv == null) continue;
    const key = `${xv}|${yv}`;
    const slot = cellMap.get(key);
    if (!slot) {
      cellMap.set(key, { best: v, count: 1 });
    } else {
      slot.count += 1;
      if ((v.wf_sharpe ?? -Infinity) > (slot.best.wf_sharpe ?? -Infinity)) {
        slot.best = v;
      }
    }
  }

  const z: (number | null)[][] = yVals.map((yv) =>
    xVals.map((xv) => {
      const slot = cellMap.get(`${xv}|${yv}`);
      return slot ? (slot.best.wf_sharpe ?? null) : null;
    }),
  );
  const text: string[][] = yVals.map((yv) =>
    xVals.map((xv) => {
      const slot = cellMap.get(`${xv}|${yv}`);
      if (!slot) return "";
      const sharpe = slot.best.wf_sharpe;
      const head = sharpe == null ? "—" : sharpe.toFixed(2);
      return slot.count > 1 ? `${head} +${slot.count - 1}` : head;
    }),
  );
  const cellHash: (string | null)[][] = yVals.map((yv) =>
    xVals.map((xv) => cellMap.get(`${xv}|${yv}`)?.best.params_hash ?? null),
  );

  const flat = z.flat().filter((v): v is number => v != null);
  const maxAbs = Math.max(0.1, ...flat.map(Math.abs));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-1">
          X:
          <select value={xKey} onChange={(e) => setXKey(e.target.value)} className="border border-border rounded px-1 py-0.5 bg-background">
            {varyingKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">
          Y:
          <select value={yKey} onChange={(e) => setYKey(e.target.value)} className="border border-border rounded px-1 py-0.5 bg-background">
            {varyingKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
      </div>
      <div className="-mx-3 sm:mx-0 overflow-x-auto">
        <div className="min-w-[480px] sm:min-w-0 px-3 sm:px-0">
          <PlotlyChart
            data={[
              {
                type: "heatmap",
                x: xVals.map(String),
                y: yVals.map(String),
                z,
                text: text as unknown as string[],
                texttemplate: "%{text}",
                textfont: { size: 10, family: "var(--font-inter)", color: PALETTE.ink },
                colorscale: PALETTE.rdbu,
                zmin: -maxAbs,
                zmax: maxAbs,
                hovertemplate: `<b>${xKey}=%{x}, ${yKey}=%{y}</b><br>WF Sharpe: %{z:.2f}<extra></extra>`,
                xgap: 1, ygap: 1,
                colorbar: { title: { text: "WF Sharpe", side: "right", font: { size: 10 } }, tickfont: { size: 10 }, thickness: 10, len: 0.9 },
              } as any,
            ]}
            layout={{
              margin: { l: 56, r: 32, t: 24, b: 36 },
              xaxis: { title: { text: xKey }, showgrid: false, fixedrange: true, type: "category" },
              yaxis: { title: { text: yKey }, showgrid: false, fixedrange: true, type: "category" },
            }}
            height={320}
            ariaLabel={`WF Sharpe heatmap over ${xKey} and ${yKey}`}
          />
        </div>
      </div>
      <p className="text-[10px] text-muted-foreground">
        Cell colour = best variant's WF Sharpe at that (x, y). "+N" badge means N other variants exist at the same cell (collapsed continuous dims).
      </p>
    </div>
  );
}

function numOrStr(a: unknown, b: unknown) {
  const an = Number(a), bn = Number(b);
  if (!isNaN(an) && !isNaN(bn)) return an - bn;
  return String(a).localeCompare(String(b));
}
```

- [ ] **Step 2: Mount it on the page**

In `web/src/app/strategies/[slug]/page.tsx`, inside `StrategyBankPage`, replace the `<VariantExplorer ... />` line with a two-pane layout:

```tsx
import { ParamHeatmap } from "@/components/strategy/param-heatmap";

// Need to pull contKeys from somewhere. For now, derive from variant params
// (every numeric key that actually varies). Future: read CONT_KEYS from the
// bundle (extend build_data.py to emit it; tracked separately).
const contKeys = Array.from(new Set(
  bundle.variants_inline.flatMap((v) => Object.keys(v.params)),
)).filter((k) => {
  const values = new Set(bundle.variants_inline.map((v) => v.params[k]));
  return values.size >= 2 && [...values].every((x) => typeof x === "number");
});

return (
  // ... existing header + KPIs ...
  <div className="grid lg:grid-cols-[3fr_2fr] gap-5 items-start">
    <VariantExplorer bundle={bundle} selectedHash={selectedHash} />
    <ParamHeatmap variants={bundle.variants_inline} contKeys={contKeys} />
  </div>
  // ... definition section ...
);
```

(Adapt the surrounding JSX as needed to keep the layout consistent with the rest of the file.)

- [ ] **Step 3: Build + smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
pnpm exec playwright test smoke strategy-bank --project=desktop --reporter=line 2>&1 | tail -10
```

Expected: build green, all smoke pass.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/components/strategy/param-heatmap.tsx web/src/app/strategies/[slug]/page.tsx
git commit -m "feat(portal): parameter heatmap on strategy bank page

2D heatmap over two continuous params (axes selectable from the params
that actually vary in this strategy's variants). Cell = best variant per
(x,y) by WF Sharpe, with +N badge when multiple variants collapse there
(spec §6.2). Mobile: horizontal scroll inside the card.

Two-pane layout on desktop (table 60% + heatmap 40%); stacks on <lg."
```

---

### Task 11: Variant slide-over (URL-driven via `?v=hash`)

**Files:**
- Create: `web/src/components/strategy/variant-slide-over.tsx`
- Modify: `web/src/app/strategies/[slug]/page.tsx` — mount the slide-over

- [ ] **Step 1: Implement the slide-over**

Create `web/src/components/strategy/variant-slide-over.tsx`:

```tsx
"use client";

import * as React from "react";
import { useRouter, usePathname } from "next/navigation";
import { X } from "lucide-react";

import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription,
} from "@/components/ui/sheet";
import type { VariantInline } from "@/lib/types";

interface Props {
  variant: VariantInline | null;
  onClose: () => void;
}

export function VariantSlideOver({ variant, onClose }: Props) {
  return (
    <Sheet open={variant != null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent side="right" className="w-full sm:max-w-md overflow-y-auto">
        {variant && (
          <>
            <SheetHeader>
              <SheetTitle className="font-heading text-xl">
                Variant <span className="font-mono text-base">{variant.params_hash.slice(0, 12)}</span>
              </SheetTitle>
              <SheetDescription>
                {variant.headline && (
                  <span className="inline-block mb-2 text-[10px] uppercase tracking-wider text-[var(--color-brand-gold-700)]">
                    Headline variant
                  </span>
                )}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-4 py-4 text-sm">
              <section>
                <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Params</h3>
                <table className="text-xs tabular w-full">
                  <tbody>
                    {Object.entries(variant.params).map(([k, v]) => (
                      <tr key={k} className="border-b border-border/40">
                        <td className="py-1 pr-2 text-muted-foreground">{k}</td>
                        <td className="py-1 text-right font-mono">{String(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
              <section>
                <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Scorecard</h3>
                <table className="text-xs tabular w-full">
                  <tbody>
                    <Row label="WF Sharpe" value={fmt(variant.wf_sharpe)} />
                    <Row label="OOS Sharpe" value={fmt(variant.oos_sharpe)} />
                    <Row label="CAGR OOS" value={fmtPct(variant.cagr_oos)} />
                    <Row label="Max DD" value={fmtPct(variant.max_dd)} />
                    <Row label="fold CoV" value={fmt(variant.cov)} />
                    <Row label="Drag" value={fmtPct(variant.slip_drag)} />
                    <Row label="Order ×" value={fmt(variant.order_mult)} />
                    <Row label="Gates" value={`${variant.n_pass ?? "—"} / ${variant.n_eval ?? "—"}`} />
                    <Row label="Tier" value={variant.tier ?? "—"} />
                  </tbody>
                </table>
              </section>
              <p className="text-[10px] text-muted-foreground border-t border-border pt-3">
                Full per-fold deep-dive available at <code>/search/{variant.params_hash}</code>.
              </p>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr className="border-b border-border/40">
      <td className="py-1 pr-2 text-muted-foreground">{label}</td>
      <td className="py-1 text-right font-mono">{value}</td>
    </tr>
  );
}

function fmt(v: number | null | undefined) { return v == null ? "—" : v.toFixed(2); }
function fmtPct(v: number | null | undefined) { return v == null ? "—" : `${(v * 100).toFixed(1)}%`; }
```

- [ ] **Step 2: Create a tiny client wrapper that reads `?v=` and renders the slide-over**

The slide-over needs `useRouter` to clear `?v=` on close, so it must be a Client Component. The parent `StrategyBankPage` is a Server Component, so we wrap.

Create `web/src/components/strategy/variant-slide-over-wrapper.tsx`:

```tsx
"use client";

import * as React from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";

import { VariantSlideOver } from "./variant-slide-over";
import type { StrategyBundleV2 } from "@/lib/types";

export function VariantSlideOverWrapper({ bundle }: { bundle: StrategyBundleV2 }) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const hash = sp.get("v");
  const variant = hash ? bundle.variants_inline.find((v) => v.params_hash === hash) ?? null : null;

  return (
    <VariantSlideOver
      variant={variant}
      onClose={() => router.push(pathname)}
    />
  );
}
```

- [ ] **Step 3: Mount the wrapper inside `StrategyBankPage`**

In `web/src/app/strategies/[slug]/page.tsx`, add the import at the top:

```tsx
import { VariantSlideOverWrapper } from "@/components/strategy/variant-slide-over-wrapper";
```

Then inside `StrategyBankPage`'s returned JSX, add the wrapper as the last element (just inside the outermost `<div>`):

```tsx
<VariantSlideOverWrapper bundle={bundle} />
```

The wrapper reads `?v=` from the URL itself via `useSearchParams`, so no props beyond the bundle are needed.

- [ ] **Step 4: Build + Playwright test slide-over**

Add to `web/tests/e2e/strategy-bank.spec.ts`:

```typescript
test("clicking a variant row opens slide-over with ?v=hash", async ({ page }) => {
  await page.goto("/strategies/");
  await page.locator("table a").first().click();
  await page.waitForLoadState("domcontentloaded");
  await page.locator("table tbody tr").first().click();
  await page.waitForURL(/\?v=[a-f0-9]+/);
  await expect(page.getByText(/^Scorecard$/i)).toBeVisible();
});
```

- [ ] **Step 5: Build + smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
pnpm exec playwright test --project=desktop --reporter=line 2>&1 | tail -10
```

Expected: build green, all smoke + strategy-bank tests pass.

- [ ] **Step 6: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/components/strategy/variant-slide-over.tsx web/src/components/strategy/variant-slide-over-wrapper.tsx web/src/app/strategies/[slug]/page.tsx web/tests/e2e/strategy-bank.spec.ts
git commit -m "feat(portal): variant slide-over driven by ?v=hash

Right-side slide-over (shadcn Sheet) opens when ?v=hash is in the URL.
Shows full params + scorecard for that variant. Closing pushes the URL
back to the bare strategy path. Linkable.

Spec §4 (variant detail) + §6.2 (heatmap drill-in destination)."
```

---

## Phase 5 — Companion routes (Tasks 12-14)

### Task 12: Demote `/search` — header copy + link rewrites

**Files:**
- Modify: `web/src/app/search/page.tsx`
- Modify: `web/src/components/search/variant-explorer.tsx` — change row link target

- [ ] **Step 1: Update the page header copy**

Edit `web/src/app/search/page.tsx`, replace `<h1>` and the paragraph:

```tsx
<h1 className="font-heading text-3xl sm:text-4xl">All variants — flat view</h1>
<p className="text-sm sm:text-base text-muted-foreground leading-relaxed">
  Cross-strategy variant table — every backtest in the search, ungrouped. Use the{" "}
  <a href="/strategies/" className="underline text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)]">Strategy Bank</a>{" "}
  if you want the curated view. {manifest.variants_count} variants × {manifest.folds_count.toLocaleString()} fold backtests.
</p>
```

- [ ] **Step 2: Rewrite row links to land in the slide-over**

Find the existing search variant-explorer component (`web/src/components/search/variant-explorer.tsx`) and change the row link href from `/search/<hash>` to a route that uses the bank: `/strategies/<resolved-sid>/?v=<hash>`. To resolve sid from hash, pull `manifest.hash_to_strategy_id` and pass it in.

Modify the variant-explorer to accept a `hashToSid: Record<string, string>` prop, default to `/search/<hash>` if the hash isn't found:

```tsx
href={hashToSid[v.params_hash]
  ? `/strategies/${hashToSid[v.params_hash]}/?v=${v.params_hash}`
  : `/search/${v.params_hash}/`}
```

In the page, pass `manifest.hash_to_strategy_id`:

```tsx
<VariantExplorer variants={variants} families={manifest.families} hashToSid={manifest.hash_to_strategy_id ?? {}} />
```

- [ ] **Step 3: Build + smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
pnpm exec playwright test smoke --project=desktop --reporter=line 2>&1 | tail -8
```

Expected: build green, smoke passes.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/app/search/page.tsx web/src/components/search/variant-explorer.tsx
git commit -m "feat(portal): demote /search — reframe + link to strategy bank

Header copy clarifies /search is now the flat power view; primary nav
moves to /strategies. Variant rows link to /strategies/<sid>/?v=<hash>
via the hash_to_strategy_id manifest lookup, opening the slide-over."
```

---

### Task 13: Convert `/search/[hash]` to redirect

**Files:**
- Modify: `web/src/app/search/[hash]/page.tsx`

- [ ] **Step 1: Replace the page body with a redirect**

Read existing file first to grab `generateStaticParams`. Then replace with:

```tsx
import { redirect, notFound } from "next/navigation";
import { getManifest } from "@/lib/data";

export async function generateStaticParams() {
  const manifest = await getManifest();
  // Keep prerendering every known hash so the redirect resolves at build time.
  return Object.keys((manifest as any).hash_to_strategy_id ?? {})
    .map((hash) => ({ hash }));
}

interface PageProps {
  params: Promise<{ hash: string }>;
}

export default async function VariantRedirect({ params }: PageProps) {
  const { hash } = await params;
  const manifest = await getManifest();
  const sid = (manifest as any).hash_to_strategy_id?.[hash];
  if (!sid) notFound();
  redirect(`/strategies/${sid}/?v=${hash}`);
}
```

- [ ] **Step 2: Build + verify**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
```

Expected: all `/search/<hash>` routes still prerender. Open one in dev mode (`pnpm dev`, then visit `/search/<some-hash>/`) — should redirect to `/strategies/.../?v=<hash>`.

- [ ] **Step 3: Smoke check**

Update `web/tests/e2e/smoke.spec.ts` if the route list includes a `/search/<hash>` example — it should now hit a 200 after redirect (Playwright follows redirects by default).

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/app/search/[hash]/page.tsx
git commit -m "feat(portal): /search/<hash> -> redirect to /strategies/<sid>/?v=<hash>

Old per-variant URLs continue to resolve via build-time redirects backed
by manifest.hash_to_strategy_id. Backlinks survive."
```

---

### Task 14: Widen `/compare/[a]/[b]` resolver

**Files:**
- Modify: `web/src/app/compare/[a]/[b]/page.tsx`

- [ ] **Step 1: Read existing compare page**

Read `web/src/app/compare/[a]/[b]/page.tsx` to understand what `a`/`b` currently resolve to (today they're treated as strategy slugs).

- [ ] **Step 2: Add a resolver that accepts strategy_id OR params_hash**

Add helper near the top:

```tsx
type ResolvedRef =
  | { kind: "strategy"; strategy_id: string; bundle: StrategyBundleV2; headlineHash: string }
  | { kind: "variant";  params_hash: string; sid: string; variant: VariantInline; bundle: StrategyBundleV2 };

async function resolveRef(token: string, manifest: any): Promise<ResolvedRef | null> {
  const knownSids = new Set<string>(
    manifest.strategies.map((s: any) => s.strategy_id ?? s.slug).filter(Boolean),
  );
  if (knownSids.has(token)) {
    const bundle = await getStrategyV2(token);
    return { kind: "strategy", strategy_id: token, bundle, headlineHash: bundle.headline_variant_hash };
  }
  const sid = manifest.hash_to_strategy_id?.[token];
  if (sid) {
    const bundle = await getStrategyV2(sid);
    const variant = bundle.variants_inline.find((v) => v.params_hash === token);
    if (variant) return { kind: "variant", params_hash: token, sid, variant, bundle };
  }
  // Legacy alias fallback
  const aliases = await getStrategyAliases();
  if (aliases[token]) return resolveRef(aliases[token], manifest);
  return null;
}
```

In the `default export`:

```tsx
const [aRef, bRef] = await Promise.all([resolveRef(a, manifest), resolveRef(b, manifest)]);
if (!aRef || !bRef) notFound();
// Render either strategy-vs-strategy, variant-vs-variant, or mixed.
```

For the equity overlay: a `strategy` ref renders its headline-variant equity (file `equity/<headlineHash>.json` if present, fall back to a slim line if not). A `variant` ref renders its own equity. Keep existing chart rendering code.

- [ ] **Step 3: Build + smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
pnpm exec playwright test smoke --project=desktop --reporter=line 2>&1 | tail -10
```

Expected: green. Existing `/compare/rotation_rank_1/clenow_som_rank_9/` URL still works (through alias fallback).

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/app/compare/[a]/[b]/page.tsx
git commit -m "feat(portal): /compare accepts strategy_id OR params_hash

Resolver maps each route param to either {kind: strategy} or
{kind: variant}, falling through the alias table for legacy slugs.
strategy_id-vs-strategy_id overlays the headline variants; mixed mode
overlays one strategy's headline against a specific variant. Spec §4."
```

---

## Phase 6 — Landing + smoke + deploy (Tasks 15-17)

### Task 15: Add "Strategy Bank" CTA + teaser to landing page

**Files:**
- Modify: `web/src/app/page.tsx`

- [ ] **Step 1: Read existing landing**

Read `web/src/app/page.tsx` (existing landing with two finalist cards).

- [ ] **Step 2: Add a section below the finalist cards**

Add a new section component below the existing finalist cards:

```tsx
import Link from "next/link";
// ... existing imports

// In the default export, after the finalist cards and before the rest:
<section className="space-y-3">
  <div className="flex items-baseline justify-between">
    <h2 className="font-heading text-2xl">Strategy Bank</h2>
    <Link href="/strategies/" className="text-sm underline text-[var(--color-brand-gold-700)] hover:text-[var(--color-brand-gold)]">
      Browse all {manifest.strategies.length} strategies →
    </Link>
  </div>
  <p className="text-sm text-muted-foreground max-w-3xl">
    Every strategy concept in the research, grouped by family × broad-shape switches. Variants like lookback 50 vs 100 days are collapsed under one strategy.
  </p>
  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
    {manifest.strategies.slice(0, 6).map((s: any) => (
      <Link
        key={s.strategy_id}
        href={`/strategies/${s.strategy_id}/`}
        className="rounded-md border border-border p-3 hover:border-foreground transition-colors block"
      >
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{s.family}</div>
        <div className="font-medium text-sm">{s.display_name}</div>
        <div className="text-xs text-muted-foreground tabular mt-1">
          {s.variant_count} variants · best OOS {s.best_oos_sharpe != null ? s.best_oos_sharpe.toFixed(2) : "—"} · tier {s.best_tier ?? "—"}
        </div>
      </Link>
    ))}
  </div>
</section>
```

- [ ] **Step 3: Build + smoke**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -5
pnpm exec playwright test smoke --project=desktop --reporter=line 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/src/app/page.tsx
git commit -m "feat(portal): Strategy Bank teaser on landing page

Top-6 strategy cards below the two finalist cards, linking to the bank
index. Narrative anchor (finalists) preserved per spec §4."
```

---

### Task 16: Mobile screenshot audit + Playwright sweep

**Files:**
- Modify: `web/tests/e2e/mobile-shots.spec.ts` (extend SHOTS list)
- Modify: `web/tests/e2e/smoke.spec.ts` (add /strategies + /strategies/<sample-sid>)

- [ ] **Step 1: Update smoke to cover the new routes**

Read `web/tests/e2e/smoke.spec.ts` and add to the route list:

```typescript
{ path: "/strategies/", label: "bank index" },
// Pick the top strategy_id from your manifest at build time:
{ path: `/strategies/${TOP_SID}/`, label: "bank strategy detail" },
```

Replace `TOP_SID` with the actual top sid from your manifest after Task 6 (e.g. `clenow_som__regime-on__rebal-M`).

- [ ] **Step 2: Extend mobile-shots spec**

Add to `web/tests/e2e/mobile-shots.spec.ts` SHOTS:

```typescript
{ path: "/strategies/", name: "08-bank-index" },
{ path: `/strategies/${TOP_SID}/`, name: "09-bank-strategy" },
```

- [ ] **Step 3: Run both locally against current deploy**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -3
PORT=3011 pnpm start &
sleep 6
LIVE_URL=http://localhost:3011 pnpm exec playwright test mobile-shots --project=mobile --reporter=line --workers=1 2>&1 | tail -10
pnpm exec playwright test smoke --project=desktop --reporter=line 2>&1 | tail -10
kill %1
```

Expected: all green. Spot-check the new mobile shots at `.tmp/mobile-shots/08-bank-index.png` and `09-bank-strategy.png`.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git add web/tests/e2e/smoke.spec.ts web/tests/e2e/mobile-shots.spec.ts
git commit -m "test(portal): smoke + mobile shots for /strategies index and detail"
```

---

### Task 17: Deploy to Railway

**Files:** none (deployment)

- [ ] **Step 1: Final build sanity**

```bash
cd C:/Users/Workstation/Desktop/BursaHack/web
pnpm build 2>&1 | tail -10
pnpm exec playwright test --project=desktop --reporter=line 2>&1 | tail -15
```

Expected: build green, all tests pass.

- [ ] **Step 2: Push to master**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
git status   # should be clean — every task above has its own commit
git log --oneline -20  # sanity check
git push origin master
```

- [ ] **Step 3: Deploy via Railway CLI**

```bash
cd C:/Users/Workstation/Desktop/BursaHack
export RAILWAY_TOKEN=$(awk -F= '/^RAILWAY_TOKEN=/{print $2}' .env | tr -d '\r\n')
railway up --service bursahack-portal --environment production --ci --no-gitignore 2>&1 | tail -10
```

Expected: `Deploy complete`.

- [ ] **Step 4: Live URL smoke**

```bash
sleep 12
for url in / /strategies/ /strategies/clenow_som__regime-on__rebal-M/ /search/ /compare/rotation__rebal-M/clenow_som__regime-on__rebal-M/; do
  curl -sSL -o /dev/null -w "HTTP %{http_code}  %{url_effective}\n" "https://bursahack-portal-production.up.railway.app${url}"
done
```

Expected: all 200.

- [ ] **Step 5: Update rh-pa memory**

Edit `C:/Users/Workstation/Desktop/rh-pa/memory/project_bursahack_portal_2026_05_18.md` adding a section noting the bank shipped (link to spec + plan paths in BursaHack). Update MEMORY.md one-liner accordingly.

```bash
cd C:/Users/Workstation/Desktop/rh-pa
git add memory/project_bursahack_portal_2026_05_18.md MEMORY.md
git commit -m "memory: BursaHack strategy bank shipped"
git push origin master
```

---

## Out of scope (deferred)

These are intentionally NOT in this plan; pick up separately if/when needed:

- New backtests / strategy authoring (registry scaffolds the *next* one; doesn't add one now)
- Live-data refresh / post-2022 universe
- Multi-user / auth / saved watchlists
- Custom dominance gate selection (frozen 4-gate set is the design)
- Parallel-coordinates, Sharpe-vs-DD scatter, sparkline grid (rejected in brainstorming)
- Per-variant equity CSV regeneration beyond the existing rotation + clenow (slide-over handles missing equity gracefully)
- Wiring `assert_holdout_safe` into every `run_search.py` call site (added as an opt-in helper in Task 4; explicit adoption is its own follow-up)
