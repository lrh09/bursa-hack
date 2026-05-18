# Strategy bank — design spec

**Status**: approved 2026-05-18
**Author**: RH (with Claude synth from 3 brainstorming agents)
**Scope**: BursaHack portal — collapse 186 parameter variants into ~12-18 strategy entries, surface them as a navigable "bank" with per-strategy variant exploration.

---

## 1. Problem

Today the portal has:

- 6 strategy families (rotation, clenow_som, momentum, reversal, tsmom, donchian_breakout)
- 186 unique parameter variants × 16 walk-forward folds = 2966 backtests
- 102 prerendered variant pages at `/search/<hash>`
- 2 hand-coded "headline" strategy pages at `/strategies/rotation_rank_1` and `/strategies/clenow_som_rank_9` — each of which is actually **one variant**, not a strategy

RH wants the portal to read as a **bank of strategies**, where each entry corresponds to a strategy concept and parameter variants nest underneath. "lookback 50 days vs 100 days" must not produce two bank entries.

## 2. Strategy unit (the grouping rule)

**`strategy = family × broad-shape switches`**

- **Broad-shape switches** are *discrete / categorical* params that fundamentally change behaviour:
  - boolean flags (`use_regime`)
  - small enumerations (`rebal_freq ∈ {W, M, Q}`, `direction ∈ {long, short}`)
- **Continuous knobs** (lookback days, top_n, MA window, weight cap, ATR window) **do not** create new strategies — they are *variants within a strategy*.

The promotion rule, frozen as a comment in `src/bursahack/signals/base.py`:

> A param is a shape key iff changing its value would make a quant call the strategy "a different strategy" in conversation.

Expected resulting count: ~12-18 strategies auto-derived from existing data.

### Single-shape families

Some families (TSMOM, Donchian, momentum, reversal) may declare zero shape keys and produce exactly **one** strategy entry. **Asymmetry is accepted** — a family with one shape-tuple is a smaller card; we don't force synthetic splits.

## 3. Data model

### 3.1 Per-class declaration

Bank metadata lives on the `Strategy` subclass as `ClassVar` attributes. Schema:

```python
class ClenowSOM(Strategy):
    name: str = "clenow_som"                      # existing field, family id

    # --- Bank metadata (NEW) ---
    DISPLAY_NAME:  ClassVar[str]            = "Clenow Stocks on the Move"
    SHORT_BLURB:   ClassVar[str]            = "Exp-regression slope × R², ATR-sized, regime-gated."
    SHAPE_KEYS:    ClassVar[tuple[str,...]] = ("use_regime", "rebal_freq")
    CONT_KEYS:     ClassVar[tuple[str,...]] = ("lookback", "trend_ma", "regime_ma", "top_n", "atr_window")
    DEFINITION_MD: ClassVar[str]            = "## Definition\n..."
    REFERENCES:    ClassVar[tuple[dict,...]] = ({"title": "Stocks on the Move", "author": "Clenow", "year": 2015},)
    SOURCE_FILE:   ClassVar[str]            = "src/bursahack/signals/clenow_som.py"
    ADDED:         ClassVar[str]            = "2026-04-12"
    HEADLINE_RULE: ClassVar[str]            = "max wf_sharpe s.t. cov<=1.0, slip_drag<=0.30"
    # SWEEP_GRID is optional and only consumed by run_search.py
    HOLDOUT_LOCKED: ClassVar[bool]          = True   # default on base class
```

**Mandatory** (build_data.py raises if missing): `DISPLAY_NAME`, `SHAPE_KEYS`, `SHORT_BLURB`, `DEFINITION_MD`, `SOURCE_FILE`, `ADDED`.

**Recommended**: `REFERENCES`, `CONT_KEYS` (else inferred as "all params not in SHAPE_KEYS"), `HEADLINE_RULE` (else default = `max wf_sharpe`).

### 3.2 strategy_id format

`strategy_id = f"{family}__{shape_slug}"` where `shape_slug` joins shape-key values in **declaration order** (frozen by `SHAPE_KEYS` tuple), with booleans normalised to readable tokens.

Examples:
- `clenow_som__regime-on__rebal-M`
- `clenow_som__regime-off__rebal-W`
- `rotation__rebal-M`
- `donchian_breakout`  (no shape keys → bare family name)

Stable across re-runs because `SHAPE_KEYS` tuple is frozen on the class.

### 3.3 Alias table for migrations

Day-1 commitment: `web/data/strategy_aliases.json` maps old IDs → current IDs. Updated whenever `SHAPE_KEYS` declarations change. Next.js middleware (or per-route resolver) returns 301 redirects on alias hits.

Initial entries:
```json
{
  "rotation_rank_1":      "rotation__rebal-M",
  "clenow_som_rank_9":    "clenow_som__regime-on__rebal-M"
}
```

### 3.4 Strategy bundle JSON shape

Path: `web/data/strategies/<strategy_id>.json`

```json
{
  "strategy_id": "clenow_som__regime-on__rebal-M",
  "family": "clenow_som",
  "display_name": "Clenow Stocks on the Move — monthly, regime on",
  "shape": { "use_regime": true, "rebal_freq": "M" },
  "short_blurb": "...",
  "definition_md": "...",
  "references": [...],
  "source_file": "src/bursahack/signals/clenow_som.py",
  "added": "2026-04-12",

  "variant_count": 16,
  "headline_variant_hash": "5a40db596edf3c9b",
  "headline_reason": "max wf_sharpe s.t. cov<=1.0, slip_drag<=0.30",

  "aggregate_metrics": {
    "wf_sharpe":  { "min": 0.41, "p25": 0.62, "median": 0.78, "p75": 0.88, "max": 0.93,
                    "best_hash": "5a40db..." },
    "oos_sharpe": { "min": 0.05, "median": 0.34, "max": 0.92, "best_hash": "5a40db..." },
    "max_dd":     { "min": -0.71, "median": -0.42, "max": -0.28 },
    "cov":        { "min": 0.78, "median": 1.05, "max": 1.34 }
  },

  "variants_inline": [
    {
      "params_hash": "5a40db596edf3c9b",
      "params": { "lookback": 60, "top_n": 30, "trend_ma": 100, "regime_ma": 200, ... },
      "wf_sharpe": 0.93, "oos_sharpe": 0.92, "cov": 1.11, "max_dd": -0.19,
      "slip_drag": 0.17, "order_mult": 1.10, "tier": "F", "headline": true
    },
    ...
  ]
}
```

Aggregates use **5-number summaries** (min/p25/median/p75/max) with `best_hash` recorded for min/max. Mean is deliberately **not** shipped (meaningless with correlated variants).

Variants ship inline as a **thin list** (~16 rows × ~30 fields ≈ 10 KB). Full per-variant detail (scorecard markdown, fold breakdown, holdout deep-dive) stays at `web/data/variants/<hash>.json` — the FE follows the link on click. No data duplication.

### 3.5 Manifest extension

`web/data/manifest.json` gains:
```json
{
  "strategies": [
    { "strategy_id": "...", "family": "...", "display_name": "...",
      "best_oos_sharpe": 0.92, "best_tier": "F", "variant_count": 16,
      "headline_variant_hash": "..." },
    ...  // ~12-18 entries
  ],
  "hash_to_strategy_id": {
    "5a40db596edf3c9b": "clenow_som__regime-on__rebal-M",
    ...  // one entry per searched variant; needed by /search/<hash> redirect
  }
}
```
(Existing `strategies` list of 2 hand-coded entries is replaced by the auto-derived list.)

**`best_tier`** is computed as the *best* (alphabetically lowest, A < B < C < F) tier across the strategy's variants.

## 4. Information architecture

### Routes

| Route | Status | Purpose |
|---|---|---|
| `/` | unchanged content | Landing; keeps the two finalist cards as narrative anchor + new "Strategy Bank" CTA below |
| `/strategies` | **NEW** | Bank index: ranked table of ~12-18 strategies |
| `/strategies/<id>` | **CHANGED** | Strategy summary: aggregates N variants; was a single-variant page |
| `/strategies/<id>?v=<hash>` | **NEW** | Variant slide-over panel; URL-linkable |
| `/search` | **DEMOTED** | Reframed as flat cross-strategy power view: "every backtest, ungrouped" |
| `/search/<hash>` | **REDIRECT** | 301 → `/strategies/<resolved-id>?v=<hash>` |
| `/compare/<a>/<b>` | **WIDENED** | `a` and `b` resolve as either strategy_id or params_hash |
| `/folds` | unchanged | Cross-family fold-stability view |
| `/methodology`, `/reports`, `/research-log` | unchanged | Narrative/reference |

### `/strategies` index page

A single sortable table, one row per strategy:

| Strategy | Family | Variants | Best OOS Sharpe | Median WF | Best tier | Gates ✓ | Sparkline |
|---|---|---|---|---|---|---|---|
| Clenow Stocks on the Move — monthly, regime on | clenow_som | 16 | 0.92 → [variant] | 0.78 | F | 9/12 | ▁▂▄▆▇ |
| Bursa Momentum Rotation — monthly | rotation | 22 | 0.29 → [variant] | 1.05 | F | 8/12 | ▁▃▅▆▇ |
| ... | | | | | | | |

**Sparkline** = the *headline variant's* equity curve (decimated to ~24 points). **Gates ✓** = gate-pass count of the headline variant, not max across the group.

Default sort: best OOS Sharpe descending. Filters: family chip-row, "has any tier ≤ D", "regime-on only". Header KPI tiles: total strategies, total variants, total backtests, holdout-cleared count.

### `/strategies/<id>` strategy page

**Top section** (group-level summary):
- Display name, family chip, shape badges (`M`, `regime-on`)
- KPI row: best OOS Sharpe (with hash badge), median WF Sharpe (with IQR), variant count, best tier, gates passed in best variant
- **Auto-generated one-liner**: built by build_data.py as `"{variant_count} variants. {cont_key_1}={min}-{max}, {cont_key_2}={min}-{max}, ... Best: {cont_key_1}={best_value}, {cont_key_2}={best_value}, ..."` over `CONT_KEYS`. No hand-writing required.

**Variant explorer** (the main interaction surface):
- **Two-pane layout** (desktop ≥ lg, stacked on smaller):
  - **Left pane (~60%)** — Sortable scorecard table. Columns: param chip, WF Sharpe, OOS Sharpe, fold-CoV, DSR_eff, PBO, CAGR, MaxDD, slippage drag, order_mult, gate-pass count.
    - Click headers to sort
    - "Hide dominated" toggle (see §6)
    - Row checkbox: 1 selected → "Open" enables (slide-over); 2 selected → "Compare" enables (→ `/compare/`)
  - **Right pane (~40%)** — 2D parameter heatmap.
    - Two `<select>`s pick X/Y axes from `CONT_KEYS`
    - Cell colour = WF Sharpe
    - Cell shows **best variant per cell** with `+N` badge if multiple variants collapse there
    - Hover-highlights table row; click drills to variant (slide-over)

**Below the explorer**:
- Overlay equity chart — all N variants as faint lines with median + best variant emphasised. Answers "robust across the variant space?" at a glance.
- Definition (rendered from `DEFINITION_MD`)
- References
- Headline-variant deep dive — collapsible accordion containing existing per-variant content (scorecard, methodology, costs, trades, risks). Default-open for the two former headline strategies; default-closed otherwise.

### `/strategies/<id>?v=<hash>` slide-over panel

Right-side slide-over, doesn't change route. Shows the variant's full detail: scorecard, gates, per-fold tables, holdout verdict, IS stability. Esc/backdrop closes. URL updates so it's linkable. `/search/<hash>` redirects to this slide-over.

### `/search` (demoted)

Page header reframed: "Cross-strategy variant table — every backtest in the search, ungrouped. Use the **Strategy Bank** if you want the curated view." Same table as today, all 186 variants, sortable on every metric. Each row links to `/strategies/<id>?v=<hash>`.

### `/compare/<a>/<b>` (widened)

`a` and `b` resolve as either strategy_id or params_hash:

1. **strategy vs strategy** — overlay each one's *best variant* equity, side-by-side metric columns; default when invoked from the bank
2. **variant vs variant** — same as today
3. **variant vs strategy** — overlay one variant against median curve of another strategy

Route resolver: if param matches a `strategy_id` in manifest, treat as strategy; else as hash.

### Landing page

Keep the two finalist cards (`rotation_rank_1` → now `rotation__rebal-M`, `clenow_som_rank_9` → `clenow_som__regime-on__rebal-M`) as narrative anchor at top. Add new "Strategy Bank" section below with link to `/strategies` and a teaser of top 5 entries.

## 5. Adding a new strategy — friction story

1. Write `src/bursahack/signals/<family>.py` with `Strategy` subclass. Fill the 6 mandatory ClassVars + recommended ones. ~30 lines of declarative metadata. (5 min)
2. Add one line to `src/bursahack/signals/__init__.py` to import the class. (10s)
3. Run sweep: `python -m bursahack.run_search --family <family>` (reads `SWEEP_GRID` if present). Appends to `search_log.jsonl` + `search_summary.csv`. (variable: 5min – 1hr)
4. Run `python scripts/build_data.py`. Auto-detects new subclass, groups rows by shape-tuple, emits bundle(s), updates `manifest.json`. (~30s)
5. `pnpm build` + `railway up`. (~5min)

**Zero FE code changes** for typical strategies. Definition renders from `DEFINITION_MD`. Aggregates compute themselves. Novel display (e.g. multi-leg spread) is the only case needing a new FE component.

## 6. Behavioural locks

### 6.1 Dominance gates (frozen)

A variant is **dominated** by another iff the other is ≥ on all four AND > on at least one:
- OOS Sharpe (higher better)
- MaxDD (less negative better)
- fold-CoV (lower better)
- DSR_eff (higher better)

Frozen set; not user-configurable. The 12-gate framework is too sparse for "strictly dominated" to be meaningful.

### 6.2 Heatmap cell semantics

When >2 params vary and the user picks 2 for axes, each cell may map to multiple variants (collapsed dims). Display rule:
- Cell colour = best variant's WF Sharpe
- Cell shows best variant's value text
- `+N` badge in corner if N ≥ 2 variants collapsed into this cell
- Click → opens best variant in slide-over; the slide-over's footer notes "N other variants at this cell" with a small "show all" link that reopens the table filtered to that cell

### 6.3 Iron-rule safety

`HOLDOUT_LOCKED: ClassVar[bool] = True` defaults on the base `Strategy` class. `run_search.py` refuses to run if any fold's `validate_end > 2019-12-31` **unless** the class declares `HOLDOUT_LOCKED = False` **AND** the CLI is invoked with `--touch-holdout`. Belt-and-braces guard against accidental holdout peeks.

`build_data.py` is read-only over `results/` and cannot trigger backtests.

## 7. Out of scope (explicitly deferred)

- New backtests / strategy authoring (this is purely a re-organisation of existing artifacts + scaffolding for the next strategy)
- Live-data refresh / post-2022 universe (separate decision, see `project_bursahack_endgame_blockers_2026_05_18`)
- Multi-user / auth / saved watchlists
- Strategy "favouriting" / personal annotations
- Parallel-coordinates plot, Sharpe-vs-DD scatter, sparkline grid (rejected during brainstorming)
- Custom dominance gate selection in FE (frozen set is the design choice)

## 8. Open questions resolved during brainstorming

| # | Question | Resolution |
|---|---|---|
| 1 | Force splits for single-shape families | No, accept asymmetry |
| 2 | Landing tone | Keep finalists + bank index below |
| 3 | Shape-key promotion rule | "Quant calls it a different strategy in conversation" |
| 4 | URL stability | `strategy_aliases.json` day-1 |
| 5 | Dominance gate set | Frozen 4: OOS Sharpe, MaxDD, fold-CoV, DSR_eff |
| 6 | Heatmap cell when >2 params vary | Best variant + `+N` badge |
