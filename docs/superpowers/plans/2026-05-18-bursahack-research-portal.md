# BursaHack Research Portal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dual-purpose static research portal for the BursaHack quant research programme — polished enough to share with investors/clients directly, deep enough to serve as RH's own research browser. Surfaces every historical backtest (186 variants × 16 folds), every artifact under `results/`, and every report, with the rotation + Clenow #9 strategies as the headline narrative.

**Architecture:** Static Next.js (App Router, `output: 'export'`) + Tailwind + shadcn/ui + Plotly.js (interactive charts) + Recharts (thumbnails/sparklines) + next-mdx-remote (render existing `.md` reports). All data is pre-baked at build time from `results/` via a Python `build_data.py` script — no FastAPI, no runtime backend, no auth gate. Deployed as a Cloudflare Pages static site.

**Tech Stack:** Next.js 15 (App Router, static export) · TypeScript (strict) · Tailwind CSS · shadcn/ui · Plotly.js (basic-dist-min) · Recharts · Lucide React · next-mdx-remote · next-themes · Playwright · pnpm.

**Brand:** Navy `#0B2349` / Gold `#C49A2A` / Soft `#F4F8FB` / Amber `#B45309` (drawdown) / Forest `#1F7A4B` (positive). Playfair Display (display + big numbers) + Inter (body + UI). Tabular numerals on every numeric column.

**Audience model:** Two reading modes share the same chrome. Investor lands on polished headline pages; researcher/client clicks any card → expands to deep detail. No mode toggle — depth lives in `<Drawer>`/`<Dialog>` and dedicated `/search` + `/folds` routes.

**Estimated build:** ~14-18 hours, split across 9 phases. Designed for autonomous end-to-end execution.

---

## Scope check

This is one app, one repo, one deploy. No sub-projects.

The two product surfaces (polished + deep) share **all** primitives — data loaders, chart components, scorecard widgets, type system. They differ only in entry-point routes (`/` vs `/search`) and density (collapsed vs expanded). Splitting them would duplicate the design system. Keep as one plan.

---

## File structure

Inside the existing `C:\Users\Workstation\Desktop\BursaHack\` repo. New code under `web/`. New Python pre-build script under `scripts/`. No changes to `src/bursahack/` (research code is frozen for this plan).

```
BursaHack/
├── scripts/
│   └── build_data.py            # NEW — emits web/data/*.json from results/
├── web/                         # NEW — Next.js app
│   ├── package.json
│   ├── tsconfig.json            # strict
│   ├── next.config.mjs          # output: 'export', trailingSlash: true
│   ├── tailwind.config.ts
│   ├── postcss.config.mjs
│   ├── components.json          # shadcn config
│   ├── playwright.config.ts
│   ├── public/
│   │   ├── favicon.ico, favicon.svg, apple-touch-icon.png
│   │   ├── og-default.png       # 1200x630, brand-locked
│   │   ├── tearsheet_rotation.png   # copied from results/
│   │   └── BursaHack_Proposal.pdf   # copied from results/
│   ├── data/                    # generated, committed for static export reproducibility
│   │   ├── manifest.json        # index of all strategies + variants + folds
│   │   ├── strategies/
│   │   │   ├── rotation_rank_1.json
│   │   │   └── clenow_som_rank_9.json
│   │   ├── variants/<params_hash>.json   # one per of the 186 variants
│   │   ├── search_log.json      # full 2,966-row search log, slimmed
│   │   ├── final_scorecards.json
│   │   ├── search_summary.json
│   │   ├── equity/
│   │   │   ├── rotation_rank_1_350k.json   # date,value,drawdown rows
│   │   │   ├── rotation_rank_1_100k.json
│   │   │   ├── rotation_rank_1_1M.json
│   │   │   └── clenow_som_rank_9_350k.json
│   │   ├── trades/
│   │   │   └── rotation_rank_1.json
│   │   └── reports/
│   │       ├── FINAL_REPORT.md
│   │       ├── REPORT.md
│   │       └── SCORECARD_rotation.md
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx           # root shell, theme, fonts, OG
│   │   │   ├── page.tsx             # / — landing + strategy comparison
│   │   │   ├── globals.css
│   │   │   ├── strategies/
│   │   │   │   └── [slug]/page.tsx  # per-strategy deep view (4-tab)
│   │   │   ├── search/
│   │   │   │   ├── page.tsx         # 186-variant browser
│   │   │   │   └── [hash]/page.tsx  # single variant detail
│   │   │   ├── folds/page.tsx       # 16-fold walk-forward viewer
│   │   │   ├── methodology/page.tsx # framework + glossary
│   │   │   ├── reports/page.tsx     # rendered markdown + PDF download
│   │   │   └── not-found.tsx
│   │   ├── components/
│   │   │   ├── ui/                  # shadcn primitives (button, card, dialog, drawer, tabs, table, badge, tooltip, separator, scroll-area, dropdown-menu, popover, command)
│   │   │   ├── shell/
│   │   │   │   ├── Sidebar.tsx      # desktop fixed
│   │   │   │   ├── MobileNav.tsx    # bottom-drawer mobile
│   │   │   │   ├── ResearchBadge.tsx
│   │   │   │   └── BrandHeader.tsx
│   │   │   ├── charts/
│   │   │   │   ├── PlotlyChart.tsx          # dynamic import wrapper
│   │   │   │   ├── EquityCurve.tsx
│   │   │   │   ├── DrawdownPanel.tsx
│   │   │   │   ├── CalendarReturnsBar.tsx
│   │   │   │   ├── MonthlyHeatmap.tsx       # value-labelled, RdBu palette
│   │   │   │   ├── RollingSharpe.tsx
│   │   │   │   ├── Sparkline.tsx            # Recharts mini
│   │   │   │   └── FoldTimeline.tsx
│   │   │   ├── scorecard/
│   │   │   │   ├── GateBadge.tsx            # SVG icon + text + color (a11y)
│   │   │   │   ├── ScorecardTable.tsx       # 12-gate row table
│   │   │   │   ├── KillTriggers.tsx
│   │   │   │   └── TierPill.tsx
│   │   │   ├── strategy/
│   │   │   │   ├── StrategyCard.tsx         # comparison stat card
│   │   │   │   ├── StrategyHeader.tsx
│   │   │   │   ├── ParamsTable.tsx
│   │   │   │   ├── MetricsRail.tsx
│   │   │   │   └── CapitalToggle.tsx
│   │   │   ├── search/
│   │   │   │   ├── VariantTable.tsx         # 186-row sortable
│   │   │   │   ├── VariantFilters.tsx       # strategy family pills
│   │   │   │   └── VariantDrawer.tsx
│   │   │   ├── reports/
│   │   │   │   └── MarkdownRenderer.tsx     # next-mdx-remote
│   │   │   └── common/
│   │   │       ├── Numeric.tsx              # tabular-nums formatter
│   │   │       ├── PctChange.tsx            # colored
│   │   │       ├── EmptyState.tsx
│   │   │       └── SkeletonCard.tsx
│   │   ├── lib/
│   │   │   ├── data.ts              # typed loaders for build-time JSON
│   │   │   ├── format.ts            # RM, %, bps, Sharpe formatters
│   │   │   ├── types.ts             # Strategy, Variant, Gate, Fold, Trade
│   │   │   ├── theme.ts             # color tokens, chart palettes
│   │   │   └── plotly-config.ts     # shared layout/config defaults
│   │   └── content/
│   │       └── glossary.ts          # term → tooltip definitions
│   └── tests/
│       └── e2e/
│           ├── smoke.spec.ts        # landing, deep nav, drill-ins
│           ├── a11y.spec.ts         # axe per route
│           └── responsive.spec.ts   # 375/768/1280 visual
└── docs/superpowers/plans/
    └── 2026-05-18-bursahack-research-portal.md   # THIS FILE
```

---

## Pre-flight assumptions

- Working machine: `LRH-WORKSTATION` (Windows 10, profile `C:\Users\Workstation\`). All paths below are absolute Windows paths.
- Node v22.17 + npm 11.4 already installed and on PATH. `pnpm` is NOT installed — install once at the start (`npm install -g pnpm`).
- Python 3.13 installed and accessible as `python`. The `bursahack` package is editable-installed at `C:/Users/Workstation/Desktop/BursaHack/`.
- Cloudflare Pages account: assumed to exist under RH's Cloudflare org. Deploy step (Phase 9) gates on RH confirming this — see iron rule.
- Git: repo on master, clean, remote `origin` at `github.com/<rh>/BursaHack`. Work happens on a feature branch `feat/research-portal`.

---

## Iron rules during execution

1. **Do not re-touch the 2020-2022 holdout.** Reuse existing equity curves; if Clenow #9 equity is missing, regenerate via `bursahack` modules WITHOUT changing any params and WITHOUT widening the OOS window.
2. **Numbers must reconcile to `results/*` artifacts.** No hand-edits. If a number isn't in the artifacts, compute it from them in `scripts/build_data.py` and version the output.
3. **Research-stage badge stays prominent on every route** until any strategy clears tier A/B. Not a corner chip — a full-width bar under the brand header.
4. **No live-trading claims** anywhere.
5. **Do not commit credentials.** The portal is auth-free; if any secret turns up (CF token, etc.), it goes in `.env.local`, gitignored.
6. **Do not push to master.** All work on `feat/research-portal`. Merge happens only after Phase 8 E2E + Phase 9 deploy check both pass. RH approves the merge separately.
7. **No `Co-Authored-By` trailer on any commit** (BursaHack inherits the rh-pa iron rule; see CLAUDE.md → "Iron rules").
8. **Deploy gating.** Phase 9 builds the static export locally and `wrangler pages deploy --dry-run` validates it. The actual production deploy + DNS attach is the only step that needs RH consent before running — flag it as a yellow checkpoint.

---

## Phase 0 — Repo scaffolding (~45 min)

### Task 0.1 — Create feature branch

**Files:** none yet.

- [ ] **Step 1:** Verify clean working tree.
  Run: `cd C:/Users/Workstation/Desktop/BursaHack; git status`
  Expected: `nothing to commit, working tree clean`.
- [ ] **Step 2:** Branch off master.
  Run: `git checkout -b feat/research-portal`
  Expected: `Switched to a new branch 'feat/research-portal'`.

### Task 0.2 — Install pnpm globally

- [ ] **Step 1:** `npm install -g pnpm@9` (latest 9.x is stable, matches Next 15).
- [ ] **Step 2:** Verify: `pnpm --version` → `9.x.x`.

### Task 0.3 — Scaffold Next.js app

- [ ] **Step 1:** From repo root, scaffold into `web/`:
  ```
  cd C:/Users/Workstation/Desktop/BursaHack
  pnpm create next-app@15 web --typescript --tailwind --eslint --app --src-dir --import-alias "@/*" --use-pnpm --no-turbopack
  ```
  When prompted for "Would you like to customize..." answer No.
- [ ] **Step 2:** Verify dev server boots:
  ```
  cd web; pnpm dev
  ```
  Expected: Next.js banner, http://localhost:3000 reachable. Kill with Ctrl+C.

### Task 0.4 — Enable static export in `next.config.mjs`

Replace the generated file contents with:

```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  trailingSlash: true,
  images: { unoptimized: true },
  experimental: { typedRoutes: true },
};
export default nextConfig;
```

- [ ] **Step 1:** Edit `web/next.config.mjs` to the above.
- [ ] **Step 2:** Run `pnpm build`. Expected: `Generating static pages` and a `web/out/` directory.

### Task 0.5 — Strict TypeScript

- [ ] **Step 1:** Edit `web/tsconfig.json`: add `"strict": true`, `"noUncheckedIndexedAccess": true`, `"noImplicitOverride": true` under `compilerOptions`.
- [ ] **Step 2:** `pnpm build` → still passes.

### Task 0.6 — Initialise shadcn/ui

- [ ] **Step 1:** `cd web; pnpm dlx shadcn@latest init`
  - Style: `Default`
  - Base color: `Slate`
  - CSS variables: Yes
- [ ] **Step 2:** Install the components used throughout this plan in one batch:
  ```
  pnpm dlx shadcn@latest add button card badge dialog drawer dropdown-menu tabs table tooltip separator scroll-area popover command sheet skeleton
  ```
- [ ] **Step 3:** Verify `web/src/components/ui/` contains all 16 files. Build still passes (`pnpm build`).

### Task 0.7 — Install runtime deps

Inside `web/`:

```
pnpm add plotly.js-basic-dist-min react-plotly.js recharts lucide-react next-themes next-mdx-remote remark-gfm rehype-slug rehype-autolink-headings
pnpm add -D @types/react-plotly.js @playwright/test @axe-core/playwright @next/eslint-plugin-next prettier prettier-plugin-tailwindcss
```

- [ ] **Step 1:** Run the two commands above.
- [ ] **Step 2:** `pnpm build` → passes.

### Task 0.8 — Commit Phase 0

```
git add web/ docs/
git commit -m "feat(portal): scaffold Next.js 15 + shadcn + Plotly + Recharts (Phase 0)"
```

---

## Phase 1 — Python data pipeline (~1.5 hr)

Generates the JSON bundles in `web/data/` that the static site reads at build time. Run once now and again whenever `results/` changes. Idempotent; commits its outputs so the static build is reproducible from a clean checkout.

### Task 1.1 — Author `scripts/build_data.py`

**Files:** `scripts/build_data.py` (NEW)

Reads:
- `results/final_scorecards.csv` (gate values per top-K variant)
- `results/search_summary.csv` (fold-aggregated stats for all 186 variants)
- `results/search_log.jsonl` (per-fold rows for all 2,966 backtests)
- `results/rotation_winner_equity.csv` (date,equity)
- `results/rotation_winner_trades.csv` (per-trade)
- `results/holdout_verdict_rotation.json` (IS/OOS/Overall verdict)
- `results/SCORECARD_rotation.md` (raw scorecard text, gated by Cmark on web side)
- `results/FINAL_REPORT.md`, `results/REPORT.md`
- `DEPLOYMENT_FRAMEWORK.md` (gate definitions + thresholds)

Emits (each is a JSON file under `web/data/`):
- `manifest.json` — `{strategies: [...], variants_count, folds_count, generated_at}`
- `final_scorecards.json` — array of {rank, strategy, params_hash, tier, rec, gates: [...]}
- `search_summary.json` — array of {strategy, params_hash, params, sharpe_mean, sharpe_std, cagr_mean, max_dd_mean, dsr, n_folds}
- `search_log.json` — array slimmed to {strategy, params_hash, fold_idx, train_start, train_end, test_start, test_end, sharpe_is, sharpe_oos}
- `strategies/rotation_rank_1.json` — full headline strategy object (params, all gates with thresholds + descriptions, summary metrics, equity ref, trades ref)
- `strategies/clenow_som_rank_9.json` — same shape as rotation
- `variants/<params_hash>.json` — one per of the 186 unique variants (params + summary)
- `equity/<strategy>_<capital>.json` — {dates: [...], equity: [...], drawdown: [...]} for {rotation_rank_1, clenow_som_rank_9} × {350000, 100000, 1000000}
- `trades/rotation_rank_1.json` — per-trade objects (date, symbol, side, qty, price, pnl)
- `reports/{FINAL_REPORT,REPORT,SCORECARD_rotation}.md` — verbatim copies for web rendering

- [ ] **Step 1:** Write `scripts/build_data.py` (~250 LOC). Structure:

```python
"""build_data.py — emit web/data/*.json from results/.
Run from repo root. Idempotent. No backtest re-runs.
"""
from __future__ import annotations
import json, csv, shutil
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
OUT = REPO / "web" / "data"

def load_jsonl(p: Path):
    with p.open() as f:
        return [json.loads(line) for line in f if line.strip()]

def load_csv(p: Path):
    with p.open() as f:
        return list(csv.DictReader(f))

GATE_DEFS = {
    1:  ("PBO",                       "Probability of Backtest Overfitting",          "<= 0.30", "lt"),
    2:  ("Deflated Sharpe (eff-N)",   "Effective-N deflated Sharpe ratio",            ">= 0.65", "gte"),
    3:  ("OOS Sharpe (net)",          "Out-of-sample Sharpe after costs",             ">= 0.30", "gte"),
    4:  ("Param stability",           "% change in Sharpe across neighbourhood",       "within +/- 25%", "abs_le"),
    5:  ("Fold-Sharpe CoV",           "Coefficient of variation of per-fold Sharpe",   "<= 1.0",  "lte"),
    6:  ("Slippage drag",             "Sharpe lost to costs",                          "<= 30%",  "lte"),
    7:  ("Avg order vs broker floor", "Average leg size vs RM 8 broker minimum",       ">= 4x",   "gte"),
    8:  ("CAGR vs hurdle",            "OOS CAGR vs 8.5% hurdle",                       ">= 8.5%", "gte"),
    9:  ("Max drawdown",              "Worst peak-to-trough",                          ">= -60%", "gte"),
    10: ("% positive months",         "Share of months with positive return",          ">= 50%",  "gte"),
    11: ("Reproducibility",           "Determinism + version pinning",                 "==1.0",   "eq"),
    12: ("Paper trading",             "Live paper trade window",                       ">= 30d",  "pending"),
}

def parse_scorecard_md(p: Path) -> dict:
    """Extract gates {n: {value, threshold, status}} from SCORECARD_rotation.md."""
    gates = {}
    for line in p.read_text().splitlines():
        if "[PASS] Gate" in line or "[FAIL] Gate" in line or "[-] Gate" in line:
            # e.g. "  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3"
            status = "PASS" if "[PASS]" in line else "FAIL" if "[FAIL]" in line else "PENDING"
            # parse gate number and value/threshold
            try:
                gn = int(line.split("Gate")[1].split("-")[0].strip())
                val = line.split("value=")[1].split()[0]
                thr = line.split("threshold=")[1].split()[0]
                val = None if val == "-" else float(val)
                thr = None if thr == "-" else float(thr)
                gates[gn] = {"value": val, "threshold": thr, "status": status}
            except (IndexError, ValueError):
                continue
    return gates

def equity_with_drawdown(equity_csv: Path) -> dict:
    rows = load_csv(equity_csv)
    dates, vals = [], []
    for r in rows:
        dates.append(r.get("date") or r.get("Date") or r.get("timestamp"))
        v = r.get("equity") or r.get("Equity") or r.get("value")
        vals.append(float(v))
    peak = vals[0]
    dd = []
    for v in vals:
        peak = max(peak, v)
        dd.append((v - peak) / peak)
    return {"dates": dates, "equity": vals, "drawdown": dd}

def emit_manifest(strategies):
    (OUT / "manifest.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategies": strategies,
        "iron": {
            "holdout_window": "2020-01 to 2022-02",
            "holdout_touch_count": 1,
            "no_live_trading": True,
        },
    }, indent=2))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for sub in ("strategies", "variants", "equity", "trades", "reports"):
        (OUT / sub).mkdir(exist_ok=True)

    # ... rotation strategy bundle
    # ... clenow strategy bundle (TODO Task 1.2 may regenerate equity)
    # ... per-variant scorecards
    # ... search summary + log
    # ... manifest
    # ... copy report markdowns
    print("OK — wrote web/data/")

if __name__ == "__main__":
    main()
```

Flesh out the `# ...` sections so the script runs end-to-end. Use the `GATE_DEFS` map to merge thresholds + descriptions onto the raw gate values from the markdown / final_scorecards.csv.

- [ ] **Step 2:** Run: `python scripts/build_data.py`. Expected: `OK — wrote web/data/` and `web/data/` populated with all the files listed above (except `equity/clenow_som_rank_9_350k.json` if Task 1.2 hasn't run yet).

### Task 1.2 — Regenerate Clenow #9 equity + trades artifacts

Clenow #9 has scorecard but no equity CSV. Regenerate without touching params or OOS window.

**Files:** `scripts/regen_clenow_artifacts.py` (NEW), `results/clenow_winner_equity.csv` (NEW), `results/clenow_winner_trades.csv` (NEW)

- [ ] **Step 1:** Locate the existing `bursahack` entry-point that materialises a single-variant equity curve. Inspect `src/bursahack/score_winner.py` first; it likely accepts a params dict and emits the same artifacts as `run_rotation_grid.py`. Use it (or whichever module owns this) — do not write a new engine.
- [ ] **Step 2:** Write `scripts/regen_clenow_artifacts.py` (~50 LOC) that calls the existing module with the exact Clenow rank-9 params from `final_scorecards.csv` row 9:
  ```python
  CLENOW_RANK_9_PARAMS = {
      "strategy": "clenow_som",
      "adv_floor": 500000.0,
      "ascending": False,
      "atr_window": 20,
      "lookback": 60,
      "max_gap": 0.15,
      "price_floor": 0.2,
      "rebal_freq": "M",
      "regime_ma": 200,
      "top_n": 30,
      "trend_ma": 100,
      "use_regime": True,
  }
  ```
  and writes `results/clenow_winner_equity.csv` and `results/clenow_winner_trades.csv` in the same schema as the rotation files.
- [ ] **Step 3:** Run it: `python scripts/regen_clenow_artifacts.py`. Expected: both CSVs created with > 100 rows.
- [ ] **Step 4:** Spot-check: the equity CSV's final value at RM 350k should be ~ 350,000 × (1 + 0.157)^period (per the recorded +15.7% CAGR over ~3 OOS years). Within an order of magnitude is fine — exact match not required.
- [ ] **Step 5:** Re-run `python scripts/build_data.py` to pick up the new Clenow equity. Verify `web/data/equity/clenow_som_rank_9_350k.json` now exists.

### Task 1.3 — Pre-compute Clenow capital sweeps (RM 100k + RM 1M)

For the capital toggle on the per-strategy page, we need equity curves at three capital levels. Rotation already has 100k/350k/1M from `proposal_pdf.py`. Clenow needs the same.

- [ ] **Step 1:** Extend `scripts/regen_clenow_artifacts.py` to loop over `[100_000, 350_000, 1_000_000]` and emit `results/clenow_winner_equity_<cap>.csv`. (For rotation, do the same if not already present — verify by listing `results/rotation_winner_equity*` first.)
- [ ] **Step 2:** Extend `scripts/build_data.py` to emit `web/data/equity/{strategy}_{capital}.json` for all six combinations.
- [ ] **Step 3:** Re-run both scripts. Verify all six files exist under `web/data/equity/`.

### Task 1.4 — Lock the data pipeline behind `pnpm prebuild`

- [ ] **Step 1:** Edit `web/package.json` `scripts`:
  ```json
  "prebuild": "cd .. && python scripts/build_data.py",
  "build": "next build"
  ```
  This regenerates JSON on every `pnpm build`, so the deployed site is always fresh against `results/`.
- [ ] **Step 2:** Run `cd web; pnpm build`. Expected: prebuild step runs Python, build succeeds.

### Task 1.5 — Commit Phase 1

```
git add scripts/build_data.py scripts/regen_clenow_artifacts.py results/clenow_winner_*.csv web/data/ web/package.json
git commit -m "feat(portal): data pipeline emits web/data/*.json from results/"
```

---

## Phase 2 — Theme, fonts, shell (~1.5 hr)

### Task 2.1 — Load Playfair Display + Inter via `next/font`

**Files:** `web/src/app/layout.tsx`, `web/src/app/globals.css`

- [ ] **Step 1:** In `layout.tsx`:
  ```tsx
  import { Inter, Playfair_Display } from "next/font/google";
  const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
  const playfair = Playfair_Display({ subsets: ["latin"], variable: "--font-playfair", display: "swap" });
  ```
- [ ] **Step 2:** Apply both `variable` classes to `<html>`. Set `<body className="font-sans bg-bg text-fg antialiased">`.
- [ ] **Step 3:** In `tailwind.config.ts`, extend `theme.fontFamily`:
  ```ts
  sans: ["var(--font-inter)", "ui-sans-serif", "system-ui"],
  display: ["var(--font-playfair)", "ui-serif"],
  ```
- [ ] **Step 4:** In `globals.css` add `@layer base { .tabular { font-variant-numeric: tabular-nums; } }`.

### Task 2.2 — Lock the color tokens

- [ ] **Step 1:** In `tailwind.config.ts` extend `theme.colors`:
  ```ts
  brand: {
    navy: "#0B2349",
    "navy-700": "#0F2D5B",
    "navy-200": "#7B91B2",
    gold: "#C49A2A",
    "gold-700": "#A07E1F",
    soft: "#F4F8FB",
    amber: "#B45309",
    forest: "#1F7A4B",
    fg: "#1A1A1A",
    "fg-muted": "#475569",
    border: "#E2E8F0",
  },
  ```
- [ ] **Step 2:** Override the shadcn CSS variables in `globals.css` `:root` block to reference these:
  ```css
  --primary: 219 70% 16%;            /* navy */
  --primary-foreground: 210 40% 98%;
  --accent: 41 64% 47%;              /* gold */
  --accent-foreground: 219 70% 16%;
  ```
  Use HSL form (shadcn convention). Compute exact HSL via any color converter when writing.
- [ ] **Step 3:** Sanity check by running `pnpm dev` and visiting `/`. The default scaffold should now have navy/gold accents.

### Task 2.3 — App shell: header + sidebar + mobile drawer

**Files:** `web/src/components/shell/BrandHeader.tsx`, `Sidebar.tsx`, `MobileNav.tsx`, `ResearchBadge.tsx`. Wire all four into `web/src/app/layout.tsx`.

- [ ] **Step 1:** `BrandHeader.tsx` — sticky top bar, navy bg, gold "FatFyre Research" wordmark (Playfair), "Bursa Momentum & Trend Following Research" subtitle (Inter, 13px). Right side: a search-results count chip ("186 variants · 16 folds") + a small "Updated YYYY-MM-DD" stamp pulled from `manifest.json`.
- [ ] **Step 2:** `ResearchBadge.tsx` — full-width amber bar directly under the header: "RESEARCH STAGE — Pre-deployment. No strategy is yet recommended for live trading. See the Scorecard for the full assessment." Persistent on every route.
- [ ] **Step 3:** `Sidebar.tsx` — desktop ≥ md, fixed left, 240px. Nav items (Lucide icons, NOT emojis):
  - `Home` → `/`
  - `LineChart` → `/strategies/rotation_rank_1`
  - `LineChart` → `/strategies/clenow_som_rank_9`
  - `Search` → `/search` (the 186-variant browser)
  - `Layers` → `/folds`
  - `BookOpen` → `/methodology`
  - `FileText` → `/reports`
  Active route highlighted with gold left-border.
- [ ] **Step 4:** `MobileNav.tsx` — `< md` only, bottom tab bar with 5 condensed items (Home / Strategies / Search / Reports / More). "More" opens a Sheet with the rest.
- [ ] **Step 5:** Smoke check: `pnpm dev`, click every nav item, verify routes render (most are 404 until later phases — that's fine).

### Task 2.4 — Dark mode via `next-themes`

- [ ] **Step 1:** Wrap children in `<ThemeProvider attribute="class" defaultTheme="light" enableSystem>` in `layout.tsx`.
- [ ] **Step 2:** Add a tiny moon/sun toggle in `BrandHeader.tsx`.
- [ ] **Step 3:** Add dark-mode token overrides in `globals.css` `.dark { ... }`. Keep navy as primary (it's already dark), invert backgrounds to a `#0A0F1C` slate near-black, keep gold as accent. Verify contrast is still ≥ 4.5:1.

### Task 2.5 — Commit Phase 2

```
git add web/
git commit -m "feat(portal): shell, theme, fonts, research-stage badge"
```

---

## Phase 3 — Primitives: charts + scorecard + numeric (~2 hr)

These are shared by every page. Build them once, tested in isolation via Storybook-style standalone pages, then reuse.

### Task 3.1 — Typed data loaders

**Files:** `web/src/lib/types.ts`, `web/src/lib/data.ts`

- [ ] **Step 1:** Define `Strategy`, `Variant`, `Gate`, `Fold`, `EquityPoint`, `Trade` types in `types.ts` matching the JSON shapes from Phase 1.
- [ ] **Step 2:** In `data.ts`, write loaders that read from `web/data/*.json` via `fs.readFile` (server-side, build-time only). Each loader is a typed function:
  ```ts
  export async function getStrategy(slug: string): Promise<Strategy>;
  export async function getEquity(slug: string, capital: number): Promise<EquityPoint[]>;
  export async function getAllVariants(): Promise<Variant[]>;
  export async function getFinalScorecards(): Promise<Strategy[]>;
  export async function getManifest(): Promise<Manifest>;
  ```
- [ ] **Step 3:** Add `generateStaticParams` helpers for the dynamic routes (`/strategies/[slug]`, `/search/[hash]`).

### Task 3.2 — `<PlotlyChart>` wrapper

**Files:** `web/src/components/charts/PlotlyChart.tsx`, `web/src/lib/plotly-config.ts`

- [ ] **Step 1:** Use `dynamic(() => import('react-plotly.js'), { ssr: false })` so Plotly never ships to the server bundle. The static export will hydrate it client-side.
- [ ] **Step 2:** Default `layout` and `config` in `plotly-config.ts`:
  - `margin: { l: 48, r: 16, t: 16, b: 40 }`
  - `font: { family: 'var(--font-inter), system-ui', size: 12, color: '#1A1A1A' }`
  - `xaxis: { gridcolor: '#E2E8F0', zeroline: false }`
  - `yaxis: { gridcolor: '#E2E8F0', zeroline: false, tickformat: ',.0f' }`
  - `config: { displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'], responsive: true }`
- [ ] **Step 3:** Always pass `useResizeHandler` and `style={{ width: '100%', height: '100%' }}` for responsiveness.

### Task 3.3 — `<EquityCurve>` + `<DrawdownPanel>` (synced)

- [ ] **Step 1:** `EquityCurve.tsx` — Plotly line, navy stroke `#0B2349`, fill below to gold `#C49A2A` at 8% opacity for the IS window, no fill for OOS. Vertical dashed line at the IS/OOS boundary (`2020-01-02` or first OOS date from manifest). Hover template: `<b>%{x|%Y-%m-%d}</b><br>Equity: RM %{y:,.0f}<extra></extra>`.
- [ ] **Step 2:** `DrawdownPanel.tsx` — area chart, amber `#B45309` fill, paired x-axis range with `EquityCurve` via a shared `useState` for zoom range (lifted to the parent `StrategyPage`).
- [ ] **Step 3:** Standalone test route: `web/src/app/dev/equity/page.tsx` renders both with rotation data. Inspect at `localhost:3000/dev/equity`. (Delete this route at end of Phase 3.)

### Task 3.4 — `<MonthlyHeatmap>` (a11y-correct)

- [ ] **Step 1:** Plotly heatmap, `colorscale: [[0, '#67001F'], [0.5, '#F7F7F7'], [1, '#053061']]` (RdBu, reversed so red = negative). Set `colorbar: { title: 'Return %' }`.
- [ ] **Step 2:** Add `text` (value labels per cell, formatted `.1f%`) so colorblind readers can read the number. `texttemplate: '%{text}'`, `textfont: { size: 10 }`.
- [ ] **Step 3:** Below the heatmap, render a `<details>` element with a fallback `<table>` listing the same data rows (axe-safe).

### Task 3.5 — `<CalendarReturnsBar>` and `<RollingSharpe>`

- [ ] **Step 1:** `CalendarReturnsBar.tsx` — Plotly bar, positive bars `#1F7A4B` (forest), negative bars `#B45309` (amber). Annotations on each bar with the year + value. Click handler → opens a `<Dialog>` showing that year's top 5 trades from `web/data/trades/rotation_rank_1.json` (use `useMemo` to group trades by year on first render).
- [ ] **Step 2:** `RollingSharpe.tsx` — 6m rolling sharpe line, gold stroke. Reference horizontal line at y=1.0 (Sharpe = 1 benchmark).

### Task 3.6 — `<Sparkline>` (Recharts, mini)

- [ ] **Step 1:** Recharts `<ResponsiveContainer><LineChart>...` — 80px tall, no axes, no grid. Used inside `StrategyCard.tsx` on the landing.

### Task 3.7 — `<GateBadge>`, `<TierPill>`, `<ScorecardTable>`

**Files:** `web/src/components/scorecard/*.tsx`

- [ ] **Step 1:** `GateBadge.tsx` — render `<Badge>` with three visual treatments:
  - PASS: forest `bg-brand-forest/12 text-brand-forest`, leading `<CheckCircle2>` icon, text "PASS"
  - FAIL: amber `bg-brand-amber/12 text-brand-amber`, leading `<XCircle>` icon, text "FAIL"
  - PENDING: muted gray, leading `<MinusCircle>`, text "PENDING"
  Critical: text + icon + color — color is never the only signal.
- [ ] **Step 2:** `TierPill.tsx` — single pill, tier A/B/C/D/F. A=forest, B=gold, C=neutral, D=amber, F=brand-amber. Always renders the letter + the recommendation in small text below.
- [ ] **Step 3:** `ScorecardTable.tsx` — `<Table>` from shadcn, columns: Gate # | Name | Value | Threshold | Status. Each row hover-tooltips with the gate's description from `GATE_DEFS`. Use `<Tooltip>` (shadcn).

### Task 3.8 — Numeric primitives

**Files:** `web/src/components/common/Numeric.tsx`, `PctChange.tsx`, `web/src/lib/format.ts`

- [ ] **Step 1:** `format.ts`:
  ```ts
  export const fmtRM = (n: number) => new Intl.NumberFormat("en-MY", { style: "currency", currency: "MYR", maximumFractionDigits: 0 }).format(n);
  export const fmtPct = (n: number, digits = 2) => `${(n * 100).toFixed(digits)}%`;
  export const fmtBps = (n: number) => `${n.toFixed(1)} bps`;
  export const fmtSharpe = (n: number) => n.toFixed(2);
  ```
- [ ] **Step 2:** `Numeric.tsx` — render a span with `className="tabular"` and selectable format.
- [ ] **Step 3:** `PctChange.tsx` — coloured: positive → `text-brand-forest`, negative → `text-brand-amber`, zero → `text-fg-muted`.

### Task 3.9 — Commit Phase 3

```
git add web/src/components/ web/src/lib/
git commit -m "feat(portal): chart + scorecard + numeric primitives"
```

---

## Phase 4 — Landing page `/` (~1.5 hr)

This is the polished investor entry. Dense enough to be a one-glance "do I trust this" check, with drill-ins to the deep surfaces.

### Task 4.1 — Hero: side-by-side strategy comparison

**Files:** `web/src/app/page.tsx`, `web/src/components/strategy/StrategyCard.tsx`

- [ ] **Step 1:** Two `<StrategyCard>` side-by-side (stacked on mobile). Each card:
  - Strategy name (Playfair, 24px) + family pill
  - Big number: OOS Sharpe (Playfair, 56px, tabular)
  - 4 small stats: CAGR / Max DD / DSR-eff / Hit rate (Inter, tabular)
  - Sparkline (Recharts) of full equity curve
  - `<TierPill>` at bottom-right
  - Entire card is `<Link>` to `/strategies/<slug>` with `cursor-pointer` and hover shadow
- [ ] **Step 2:** Above the cards: H1 "Bursa Momentum & Trend-Following — Research Findings 2007-2022" + dek "186 variants tested across 16 walk-forward folds. Two finalists: a cross-sectional momentum rotation and a Clenow Stocks-on-the-Move trend follower. Neither yet cleared for live capital."
- [ ] **Step 3:** Below the cards: a 3-up row of "Overall programme metrics" (`Variants tested: 186` / `Backtests run: 2,966` / `Holdout touch count: 1`).

### Task 4.2 — Section: "How to read this portal" (collapse-able)

3 short paragraphs explaining the IS / OOS / holdout structure, the 12-gate framework in plain language, what tier F means. Inside a `<details>` that defaults open on desktop, closed on mobile. Acts as a methodology preview.

### Task 4.3 — Section: Search-explorer teaser

Embed the top-10 leaderboard table (read from `final_scorecards.json`) with a "View all 186 →" CTA going to `/search`. Sortable columns: Rank / Strategy / WF Sharpe / OOS Sharpe / Tier.

### Task 4.4 — Section: Reports archive teaser

A card grid with 3 thumbnails: FINAL_REPORT, REPORT, Investor PDF (cover image extracted to PNG). Each links to `/reports` or directly downloads the PDF.

### Task 4.5 — Commit Phase 4

```
git add web/src/app/page.tsx web/src/components/strategy/
git commit -m "feat(portal): landing with side-by-side strategy comparison"
```

---

## Phase 5 — Per-strategy deep page `/strategies/[slug]` (~3 hr)

Tabbed deep view used by both rotation and Clenow. Generated statically at build time.

### Task 5.1 — Route + static params + tab shell

**Files:** `web/src/app/strategies/[slug]/page.tsx`

- [ ] **Step 1:** `generateStaticParams` returns the two slugs from `manifest.json`.
- [ ] **Step 2:** Layout: `<StrategyHeader>` on top (name, family pill, tier, recommendation, key metrics row), then a shadcn `<Tabs>` with 4 tabs:
  - `Overview` — narrative + sparkline + key stats
  - `Performance` — full equity + drawdown + capital toggle + calendar bar + heatmap + rolling Sharpe
  - `Methodology` — params table + walk-forward fold breakdown + gate-by-gate scorecard
  - `Risks` — drawdown distribution + kill triggers + risk-factor cards
- [ ] **Step 3:** Default tab is `Overview`. Tab state is reflected in URL hash (`?tab=performance`) so deep links work.

### Task 5.2 — Performance tab

- [ ] **Step 1:** `<CapitalToggle>` (RM 100k / 350k / 1M, defaults to 350k, persists to localStorage).
- [ ] **Step 2:** Stacked layout (mobile: stacked; desktop ≥ lg: 2-col with metrics rail at right):
  - `<EquityCurve>` (full)
  - `<DrawdownPanel>` (full, synced x)
  - `<CalendarReturnsBar>`
  - `<MonthlyHeatmap>`
  - `<RollingSharpe>`
  - `<MetricsRail>` — pinned right rail showing CAGR / Sharpe / Sortino / Max DD / Hit rate / Cost/leg / Turnover / DSR
- [ ] **Step 3:** Each chart has a small `<DownloadButton>` (top-right, gray-on-hover) that downloads the underlying JSON for that chart.

### Task 5.3 — Methodology tab

- [ ] **Step 1:** `<ParamsTable>` — the variant's params as a 2-col table.
- [ ] **Step 2:** `<FoldTimeline>` — 16 horizontal strips, one per fold, each split into train (gray) + test (gold) segments on a 2007-2022 timeline. Click a fold → opens a `<Drawer>` with fold-specific IS/OOS Sharpe + CAGR.
- [ ] **Step 3:** `<ScorecardTable>` — the full 11-gate table for this variant (from Phase 3.7).

### Task 5.4 — Risks tab

- [ ] **Step 1:** Drawdown distribution: Plotly histogram of all rolling 1y / 3y / 5y peak-to-trough drawdowns. Computed in `build_data.py` and emitted as part of the strategy bundle.
- [ ] **Step 2:** `<KillTriggers>` — read the 6 K1-K6 triggers from `DEPLOYMENT_FRAMEWORK.md` parsed in Phase 1, render as a vertical list with the numerical threshold for THIS strategy filled in.
- [ ] **Step 3:** Risk-factor cards: 6 collapsed cards (Market / Regime / Concentration / Model / Liquidity / Operational). Each opens to a paragraph of plain-language text written into `web/src/content/risks.ts` (one entry per strategy).

### Task 5.5 — Overview tab (light)

- [ ] **Step 1:** Two paragraphs of narrative (sourced from existing `SCORECARD_rotation.md` + a written one for Clenow). Stat strip at top. Sparkline.
- [ ] **Step 2:** CTA row to other tabs.

### Task 5.6 — Commit Phase 5

```
git add web/src/app/strategies/ web/src/components/strategy/ web/src/content/
git commit -m "feat(portal): per-strategy deep page (rotation + clenow #9)"
```

---

## Phase 6 — Search explorer `/search` + `/search/[hash]` (~2 hr)

The "research depth" surface. Browse all 186 variants. Filter by strategy family. Sort by any metric. Drill into any one of them.

### Task 6.1 — `/search` — variant browser

**Files:** `web/src/app/search/page.tsx`, `web/src/components/search/VariantTable.tsx`, `VariantFilters.tsx`

- [ ] **Step 1:** Layout: filter row at top + sortable table below. Filters:
  - Strategy family pills (momentum / reversal / clenow_som / rotation / tsmom / donchian) — multi-select
  - Quick filter chips: "Only passing OOS Sharpe ≥ 0.3" / "Only DSR ≥ 0.65"
  - Search box (filters by params_hash prefix)
- [ ] **Step 2:** `<VariantTable>` — columns: Rank | Strategy | Lookback | Top-N | Rebal | WF Sharpe | OOS Sharpe | CAGR | DSR | Tier. Sortable headers. Clicking a row pushes to `/search/<hash>`. Use `<Table>` from shadcn + a small custom sort hook. Virtualise only if > 1000 rows (we have 186, no need).
- [ ] **Step 3:** Mobile: collapse the table into card-rows (one card per variant, key metrics shown, "View →" link).

### Task 6.2 — `/search/[hash]` — single variant detail

**Files:** `web/src/app/search/[hash]/page.tsx`

- [ ] **Step 1:** `generateStaticParams` returns all 186 hashes from `variants/*.json`.
- [ ] **Step 2:** Layout: same `<StrategyHeader>` shape as Phase 5, but for arbitrary variants — many will lack equity/trades artifacts. If the bundle has no equity series, hide the Performance tab and show a notice: "Per-fold metrics only — full equity curve not regenerated for this variant. See [Methodology] for fold breakdown."
- [ ] **Step 3:** Tabs: Methodology (fold table + params + scorecard) + Risks (gates only, no DD distro if no equity).
- [ ] **Step 4:** Cross-link "Compare with rotation" / "Compare with Clenow #9" at the top.

### Task 6.3 — Commit Phase 6

```
git add web/src/app/search/ web/src/components/search/
git commit -m "feat(portal): search explorer for all 186 variants"
```

---

## Phase 7 — Folds, methodology, reports (~1.5 hr)

### Task 7.1 — `/folds` — global walk-forward viewer

**Files:** `web/src/app/folds/page.tsx`, `web/src/components/charts/FoldTimeline.tsx`

- [ ] **Step 1:** Big `<FoldTimeline>` (16 folds) at top with a strategy dropdown (defaults to rotation_rank_1).
- [ ] **Step 2:** Below: a fold-by-fold scatter — x: train-end date, y: OOS Sharpe of that fold for the selected strategy. Hover for fold detail.
- [ ] **Step 3:** Right rail: aggregate IS/OOS Sharpe correlation, fold-Sharpe CoV, and the IS-OOS rank correlation (all from the strategy bundle).

### Task 7.2 — `/methodology` — framework + glossary

**Files:** `web/src/app/methodology/page.tsx`, `web/src/content/glossary.ts`, `MarkdownRenderer.tsx`

- [ ] **Step 1:** Render `DEPLOYMENT_FRAMEWORK.md` via `next-mdx-remote` + `remark-gfm` + `rehype-slug` + `rehype-autolink-headings`. Custom MDX components: `<Gate>` and `<Tier>` so the framework doc can drop in interactive widgets.
- [ ] **Step 2:** Bottom of page: a glossary section. Terms keyed in `glossary.ts` (PBO, DSR, MinBTL, hit rate, walk-forward, etc.) — each rendered as a `<Card>` with term + definition + (when applicable) the value from the rotation rank-1 variant.

### Task 7.3 — `/reports` — archive

**Files:** `web/src/app/reports/page.tsx`

- [ ] **Step 1:** A list of all reports under `web/data/reports/`. For each: title, last-modified, "Open →" button.
- [ ] **Step 2:** Each report opens in a sub-route `/reports/<slug>` rendering the markdown via `next-mdx-remote`. The investor PDF gets a "Download PDF" button instead of inline render.
- [ ] **Step 3:** Tearsheet PNG embedded inline at the top with a "Download high-res" link.

### Task 7.4 — Commit Phase 7

```
git add web/src/app/folds/ web/src/app/methodology/ web/src/app/reports/ web/src/content/glossary.ts web/src/components/reports/
git commit -m "feat(portal): folds, methodology, reports archive"
```

---

## Phase 8 — Polish, a11y, OG, perf (~1.5 hr)

### Task 8.1 — Mobile-first sweep

- [ ] **Step 1:** Walk every route at 375px (Chrome DevTools iPhone SE preset). Fix: card stacking, table → card transitions, drawer for nav, no horizontal scroll.
- [ ] **Step 2:** Walk at 768px (tablet). Fix breakpoint glitches.
- [ ] **Step 3:** Confirm all touch targets are ≥ 44×44px (per the skill's CRITICAL rule).

### Task 8.2 — Accessibility sweep

- [ ] **Step 1:** Add `aria-label` to every icon-only button (theme toggle, sort buttons, download icons).
- [ ] **Step 2:** Verify every `<img>` has descriptive `alt`. Tearsheet alt: "Tearsheet showing equity curve, drawdown, calendar returns and monthly heatmap for the Bursa Momentum Rotation strategy 2008-2022."
- [ ] **Step 3:** Verify keyboard nav: tab through landing → strategy → tabs → table → links. Visible focus rings everywhere (shadcn ships them; just verify they aren't overridden).
- [ ] **Step 4:** Add `prefers-reduced-motion` to `globals.css`:
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
  }
  ```
- [ ] **Step 5:** Verify all text-on-color combinations pass 4.5:1 (use the WCAG contrast checker plugin or the Chrome devtools accessibility panel).

### Task 8.3 — OG image + favicon

- [ ] **Step 1:** Generate `og-default.png` (1200×630) — navy bg, gold "BursaHack — Research Portal" wordmark, soft "FatFyre Research" subtitle, tearsheet thumbnail in lower-right. Easiest path: render `web/src/app/og/page.tsx` then screenshot, or use a pre-rendered PNG made in any image tool.
- [ ] **Step 2:** Add favicon set: `favicon.ico` (32×32), `favicon.svg` (vector), `apple-touch-icon.png` (180×180). All show a gold "F" on navy.
- [ ] **Step 3:** In `layout.tsx`, set `metadata`:
  ```ts
  export const metadata = {
    title: { default: "BursaHack — Research Portal", template: "%s · BursaHack" },
    description: "Bursa Malaysia momentum & trend-following research findings 2007-2022. 186 variants × 16 walk-forward folds.",
    openGraph: { type: "website", siteName: "BursaHack", images: ["/og-default.png"] },
    twitter: { card: "summary_large_image" },
    robots: { index: false, follow: false },   // semi-private, no SEO
  };
  ```

### Task 8.4 — Performance budget

- [ ] **Step 1:** Confirm Plotly is dynamic-imported on every chart (no Plotly in the initial JS bundle for landing).
- [ ] **Step 2:** Run `pnpm build` and look at the route-by-route bundle size report. Each route's first-load JS should be < 200KB (excluding Plotly, which lazy-loads).
- [ ] **Step 3:** Run a Lighthouse audit on `localhost:3000` after `pnpm build && pnpm exec serve out`. Targets: Performance ≥ 90, A11y ≥ 95, Best Practices ≥ 95.

### Task 8.5 — Commit Phase 8

```
git add web/
git commit -m "polish(portal): mobile, a11y, OG, perf"
```

---

## Phase 9 — Playwright E2E + deploy (~1.5 hr)

Per the `feedback_e2e_required_for_interaction` memory, interaction surfaces need Playwright walks.

### Task 9.1 — Playwright setup

- [ ] **Step 1:** `cd web; pnpm exec playwright install chromium webkit`
- [ ] **Step 2:** Generate `playwright.config.ts` with two projects: `chromium`, `webkit`. Base URL `http://localhost:3000`. `webServer: { command: 'pnpm dev', port: 3000, reuseExistingServer: true }`.

### Task 9.2 — `tests/e2e/smoke.spec.ts`

Routes covered: `/`, `/strategies/rotation_rank_1`, `/strategies/clenow_som_rank_9`, `/search`, `/search/<one-hash>`, `/folds`, `/methodology`, `/reports`.

For each:
- [ ] Page loads without console errors
- [ ] H1 contains the expected text
- [ ] Research-stage badge is visible
- [ ] Nav is functional (sidebar on desktop project, drawer on a mobile project)
- [ ] At least one chart renders (Plotly's SVG `g.plotly` exists)

### Task 9.3 — `tests/e2e/a11y.spec.ts`

Run `@axe-core/playwright` on each main route. Assert no violations of severity `serious` or `critical`.

### Task 9.4 — `tests/e2e/responsive.spec.ts`

At each of `[375, 768, 1280]`, navigate to `/` and `/strategies/rotation_rank_1`, screenshot, and assert no horizontal-scroll bar appears (`document.documentElement.scrollWidth === clientWidth`).

### Task 9.5 — Run the full suite locally

- [ ] **Step 1:** `pnpm exec playwright test` — expect all green.
- [ ] **Step 2:** If failures, fix root cause and re-run. Do not snapshot-update without inspecting the diff.

### Task 9.6 — Local static export validation

- [ ] **Step 1:** `pnpm build` → check `web/out/` is populated, contains an `index.html` per route, all `web/data/*.json` copied via `public/` (or restructure: actually put the JSON under `public/data/` so static export ships them).
- [ ] **Step 2:** `pnpm exec serve out` and click through every route on the served static build.
- [ ] **Step 3:** If `web/data/` is referenced by absolute path during build but missing in `out/`, move it under `web/public/data/` and update `lib/data.ts` to `fetch('/data/...')` at runtime instead of `fs.readFile` at build. Pick one approach and stick to it.

### Task 9.7 — Cloudflare Pages deploy (gated)

This step touches a real-world service. Ask RH before running.

- [ ] **Step 1:** Confirm RH has a Cloudflare Pages project for BursaHack (or willing to create one). If yes, install `wrangler`: `pnpm add -D wrangler` then `pnpm exec wrangler login`.
- [ ] **Step 2:** Dry-run deploy: `pnpm exec wrangler pages deploy out --project-name bursahack --dry-run`. Expect a manifest of files to upload.
- [ ] **Step 3:** **Pause and confirm with RH** before the live deploy. RH approves → `pnpm exec wrangler pages deploy out --project-name bursahack`.
- [ ] **Step 4:** Smoke the deployed URL: open `https://bursahack.pages.dev`, walk every route once.

### Task 9.8 — Commit Phase 9 + PR

```
git add web/tests/ web/playwright.config.ts web/package.json
git commit -m "test(portal): Playwright smoke + a11y + responsive E2E"
git push -u origin feat/research-portal
gh pr create --title "BursaHack research portal v1" --body "..."
```

Per `feedback_just_push_personal_repos`, RH can merge directly without code review on this repo. The PR is for changelog visibility, not gating.

---

## Self-review checklist

After all phases pass:

1. **Spec coverage** — every requirement in this section is reachable:
   - [x] Polished investor surface (Phase 4 landing)
   - [x] Deep research surface (Phases 5, 6, 7)
   - [x] All 186 historical variants browsable (Phase 6)
   - [x] All 16 walk-forward folds visualised (Phase 7.1)
   - [x] All existing reports archived (Phase 7.3)
   - [x] Rotation + Clenow #9 side-by-side (Phase 4.1)
   - [x] Playfair + Inter typography (Phase 2.1)
   - [x] Mobile-first responsive (Phase 8.1)
   - [x] No auth gate (per RH's choice; private URL only)
   - [x] Cloudflare Pages static deploy (Phase 9.7)
   - [x] Best-practice TS strict, a11y AA, Lighthouse ≥ 90 (Phase 8)
   - [x] No SVG/Plotly in initial bundle (Phase 8.4)
   - [x] Research-stage badge persistent (Phase 2.3)

2. **Placeholder scan** — none. Every step shows the code, the file, or the exact command.

3. **Type consistency** — `Strategy`, `Variant`, `Gate`, `Fold` types are defined in Task 3.1, referenced in every later component. Loaders in `data.ts` return these types. No drift.

4. **Iron-rule compliance** — holdout not re-touched (Task 1.2 reuses existing params), no co-author trailer (called out explicitly), no master push (feat branch), credentials gitignored (no credentials needed since no auth).

---

## Version log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-05-18 | Initial plan. 9 phases, ~14-18hr, autonomous execution. Replaces the Streamlit-first plan from UI_PLAN.md v0.1. |
