# BursaHack — Investor Dashboard UI Plan

**Status**: Plan only. No code lands until next session.
**Adopted**: 2026-05-18
**Strategy**: Two-stage rollout — Streamlit MVP this week, Next.js V1 later.

---

## 0. Goal

Build an **investor-facing research dashboard** for the Bursa Momentum Rotation strategy. Investor opens a private URL, lands on a polished dashboard, explores the strategy's case interactively — not by reading a static report.

**This is an app, not a page.** Multi-view nav, interactive charts, drill-downs, persistent state across sessions.

**This is investor-facing, not operator-facing.** Read-only. No parameter editing. No backtest re-running. The strategy is fixed; the investor explores the data.

---

## 1. Audience & Constraints

### Audience
- **Primary**: people you've identified to share this with in the next 1-3 months (family / personal network)
- **NOT public** — site is private, password/auth-gated
- 5-15 users maximum in this stage

### Constraints
- Strategy is still pre-deployment (tier F per DEPLOYMENT_FRAMEWORK.md). Site must be honest about research stage.
- Content must reconcile to actual artifacts in `results/` — no hand-tweaked numbers.
- Mobile-tolerable (some investors will open the link on their phone)
- Bursa-Malaysia context (RM, EPF, KLCI references)

---

## 2. Two-Stage Rollout

### Stage 1 — Streamlit MVP (ship this week, ~5 hr build)

Goal: working dashboard, decent polish, deploy-able to investors **this week**.

Tradeoffs accepted:
- Streamlit's default chrome (sidebar, top bar) cannot be fully hidden
- Layout grid is constrained
- Mobile renders OK but not native-app-grade

Why ship this first: use it as the **content-and-UX prototype**. Iterate on what investors actually care about, what charts they zoom in on, what questions they ask. The answers inform the Next.js V1 spec.

### Stage 2 — Next.js V1 (later, ~18 hr build across 3-4 sessions)

Goal: production-grade investor dashboard. Polish that justifies the strategy quality underneath.

Built once the Streamlit version has been used by a handful of real investors and the content/UX is settled. Reuses:
- All content (markdown sections, methodology, risk language)
- All chart data (JSON exports already needed by Streamlit)
- The FastAPI backend (built in Stage 1 if we go API-driven, OR in Stage 2 from scratch)

---

## 3. Stage 1 — Streamlit MVP Detailed Plan

### 3.1 Pages

**4 pages, sidebar navigation:**

```
┌──────────────────┐
│  ★ Overview      │   ← landing
│  📈 Performance  │
│  🔬 Methodology  │
│  ⚖️  Scorecard   │
│                  │
│  (auth panel)    │
│  Logged in as:   │
│  email@...       │
└──────────────────┘
```

### 3.2 Page-by-Page Wireframes

#### Page 1 — Overview (landing)

```
┌──────────────────────────────────────────────────────────────┐
│  BURSA MOMENTUM ROTATION                  [Research Stage]  │
│  Dual-Slope Cross-Sectional Momentum                         │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─ IN-SAMPLE ────┐  ┌─ OUT-OF-SAMPLE ┐  ┌─ OVERALL ───┐   │
│  │  +19.45%       │  │  +4.15%        │  │  +17.29%    │   │
│  │  CAGR          │  │  CAGR          │  │  CAGR       │   │
│  │  Sharpe 0.81   │  │  Sharpe 0.29   │  │  Sharpe 0.73│   │
│  │  Max DD -48%   │  │  Max DD -48%   │  │  Max DD -48%│   │
│  │  2008-2019     │  │  2020-2022 ←OOS│  │  2008-2022  │   │
│  └────────────────┘  └────────────────┘  └─────────────┘   │
│                                                              │
│  ─────  Mini equity curve sparkline (full history) ─────    │
│                                                              │
│  How it works                                                │
│  Every month, on the first business day, the strategy ranks │
│  all eligible Bursa Malaysia stocks by a composite trend     │
│  score, selects the top 20, and rebalances...                │
│                                                              │
│  [→ See full performance]  [→ See methodology]              │
└──────────────────────────────────────────────────────────────┘
```

Interactions:
- Sparkline is interactive (hover for date + value)
- The 3 stat cards are clickable → jump to Performance page with that window pre-selected
- The 2 CTAs at bottom navigate to other pages

Data sources:
- `results/rotation_winner_equity.csv` (sparkline)
- `results/SCORECARD_rotation.md` or `holdout_verdict_rotation.json` (numbers)

---

#### Page 2 — Performance (the meat)

```
┌──────────────────────────────────────────────────────────────┐
│  Performance                                                 │
├──────────────────────────────────────────────────────────────┤
│  ┌─ Filters ────────────────────────────────────────────┐  │
│  │ Capital: [ RM 100k | RM 350k✓ | RM 1M ]              │  │
│  │ Window:  [ IS | OOS | Overall✓ | Custom range ]      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Equity Curve (Plotly, interactive zoom) ────────────┐  │
│  │ RM 4M                                          ▁▂▄  │  │
│  │      ◢▆▇▇▇▆▅▄▅▆▇█▇▆▅▅▆▇█████▇▆▅▅▆▇▇█▇▆        │  │
│  │  ◢▆█▆▅                                     ▁ |OOS|   │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌─ Drawdown (synced x-axis) ───────────────────────────┐  │
│  │  ▁▁▁ ▃▆█▆▃ ▁▁▁ ▁▂▃▁ ▁▁▁ ▃▆█▆▃ ▁▁▁ ▁▂▃▁ ▁▁▁   │  │
│  │                                                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Metrics (right rail) ───────┐                           │
│  │ CAGR:        +17.29%         │                           │
│  │ Sharpe:       0.73           │                           │
│  │ Sortino:      0.82           │                           │
│  │ Max DD:      -47.93%         │                           │
│  │ Hit rate:    54.1%           │                           │
│  │ Cost/leg:    24.9 bps        │                           │
│  │ Turnover:    11.96x          │                           │
│  └──────────────────────────────┘                           │
│                                                              │
│  ── Calendar-year returns ─────────────────────────────     │
│  [bar chart, click any year to see monthly drill-in]        │
│                                                              │
│  ── Monthly returns heatmap ───────────────────────────     │
│  [heatmap, hover for value, click cell to see that month's │
│   top trades]                                                │
└──────────────────────────────────────────────────────────────┘
```

Interactions:
- Capital toggle: 3-button radio. Changes which equity series is shown. **Cached** — switching is instant after first load.
- Window toggle: highlights/clips the equity curve to that window. Drawdown panel recomputes from clipped window.
- "Custom range" reveals a date-range slider.
- Equity curve: drag-to-zoom (Plotly default), hover for tooltip (date, value, drawdown).
- Year-bar click: opens a side panel showing that year's monthly returns + biggest contributing trades.
- Monthly heatmap cell click: same — that month's trade detail.

Data sources:
- `results/rotation_winner_equity.csv` (full history at RM 350k)
- Pre-computed equity curves at RM 100k and RM 1M (run via `proposal_pdf.py` capital sweep)
- `results/rotation_winner_trades.csv` (for drill-ins)

---

#### Page 3 — Methodology

```
┌──────────────────────────────────────────────────────────────┐
│  Methodology                                                 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  1. The Strategy in 60 seconds                              │
│     [process diagram: 7 steps in a horizontal flow]         │
│     Each step expandable on click for detail.                │
│                                                              │
│  2. Investment Thesis                                       │
│     Cross-sectional momentum is one of the most robustly... │
│                                                              │
│  3. Walk-Forward Validation                                 │
│     [16-fold visualizer: each fold is a coloured strip on  │
│      a timeline; slider to step through folds]              │
│                                                              │
│  4. The Brute-Force Search                                  │
│     186 variants tested. [strategy family pie chart].       │
│     [Top-10 leaderboard table, sortable by WF / OOS Sharpe] │
│                                                              │
│  5. Out-of-Sample Discipline                                │
│     The 2020-2022 holdout was touched exactly once...       │
└──────────────────────────────────────────────────────────────┘
```

Interactions:
- Process diagram: click each step → expand modal/inline panel with formula or detail
- Fold visualizer: slider scrubs through the 16 walk-forward folds, each fold highlights its train + validate windows on a timeline
- Top-10 leaderboard: sortable, click any row → modal with that variant's scorecard

Data sources:
- DEPLOYMENT_FRAMEWORK.md (for the process steps)
- `results/search_summary.csv` (top-10 leaderboard)
- `results/final_scorecards.csv` (per-variant scorecards)

---

#### Page 4 — Scorecard & Risks

```
┌──────────────────────────────────────────────────────────────┐
│  Scorecard & Risks                                           │
├──────────────────────────────────────────────────────────────┤
│  Strategy passes/fails per the deployment framework:        │
│                                                              │
│  ┌─ 12-Gate Scorecard ──────────────────────────────────┐  │
│  │ 🟢 PBO                       0.00  < 0.30            │  │
│  │ 🟢 Deflated Sharpe (eff-N)   0.66  > 0.65            │  │
│  │ 🔴 OOS Sharpe (net)          0.29  > 0.30           │  │
│  │ 🔴 Param stability          -33%   ±25% limit       │  │
│  │ 🟢 Fold-Sharpe CoV           0.80  < 1.0             │  │
│  │ 🔴 Slippage drag             54%   < 30%            │  │
│  │ 🔴 Order vs broker floor     1.3x  ≥ 4x             │  │
│  │ 🔴 CAGR vs hurdle            4.2%  > 8.5%           │  │
│  │ 🟢 Max DD                   -48%   < 60%             │  │
│  │ 🟢 % positive months         60%   ≥ 50%             │  │
│  │ 🟢 Reproducibility           ✓                       │  │
│  │ ⚪ Paper trading              —    ≥ 30 days        │  │
│  │                                                       │  │
│  │ Tier: F     Recommendation: NO GO (recommended)      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  Hover any gate → tooltip with definition + why threshold   │
│                                                              │
│  Drawdown distribution                                       │
│  [histogram of all rolling 1y, 3y, 5y peak-to-trough]       │
│                                                              │
│  Risk factors                                                │
│  [6 cards: Market, Regime, Concentration, Model, Liquidity, │
│   Operational. Each expandable]                              │
│                                                              │
│  Kill triggers (if deployed)                                │
│  [the 6 K1-K6 triggers with their numerical thresholds      │
│   computed against the current strategy]                     │
└──────────────────────────────────────────────────────────────┘
```

Interactions:
- Gate hover: tooltip with definition + threshold rationale (sourced from DEPLOYMENT_FRAMEWORK.md)
- Gate click (for failed gates): expand with details on why it fails + what would fix it
- Risk-factor cards: expand on click
- Kill-trigger table: read-only display of the numerical thresholds

Data sources:
- `results/SCORECARD_rotation.md` (raw scorecard)
- `results/final_scorecards.csv` (gate values)
- DEPLOYMENT_FRAMEWORK.md (gate definitions and thresholds)

---

### 3.3 Tech details

**Stack**:
- Streamlit 1.30+ with multipage app structure (`pages/` folder)
- Plotly for all charts (not matplotlib — interactivity matters)
- `streamlit-option-menu` or custom CSS for sidebar nav polish
- `streamlit-aggrid` for the leaderboard table (sortable, paginated, polished)
- Pillow / base64 for embedded brand assets

**State management**:
- `st.session_state` for filter selections (capital, window) — persists across page nav
- `st.cache_data` for CSV loads
- `st.cache_resource` for the (heavier) parquet panel if any page needs it

**Caching strategy**:
- Equity curves at all 3 capital levels pre-computed and saved as CSVs on first load
- Switching capital → CSV lookup, ~50ms
- No live backtest computation in the user-facing app

### 3.4 Styling

**Theme** (`.streamlit/config.toml`):
```toml
[theme]
primaryColor = "#C49A2A"          # gold (same as PDF)
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F4F8FB"
textColor = "#1A1A1A"
font = "sans serif"
```

**Custom CSS** (injected via `st.markdown`):
- Hide Streamlit hamburger menu, "Made with Streamlit" footer
- Custom navy header bar (matching PDF)
- Tighter card padding
- Mobile media queries

**Brand**:
- Navy `#0B2349` (matches PDF)
- Gold `#C49A2A`
- Red `#A8332B` (drawdowns, fails)
- Green `#1F7A4B` (positive returns)
- Soft gray `#F4F8FB` (card backgrounds)

**Typography**: Inter from Google Fonts (modern fintech, readable).

### 3.5 Auth strategy

**Option A — Cloudflare Access (recommended)**:
- Self-host Streamlit on Railway
- Cloudflare Tunnel from Railway → custom domain
- Cloudflare Access magic-link with email allowlist
- Investor flow: opens link → enters email → magic link in inbox → in
- Revocable per-user via CF dashboard
- Free for < 50 users

**Option B — Simple password gate**:
- Streamlit session_state with a single shared password
- `secrets.toml` stores the hash
- Send password in the same email as the URL
- Easier setup, less revocable

Default: **A**.

### 3.6 Hosting

- **Railway service** (you already have a Railway account from MatchUp)
- `Dockerfile` to containerise the Streamlit app
- Deploy via `railway up` or git-push integration
- Static results files (CSVs, JSONs) included in the image; rebuild image to update data
- ~$5/mo per service

### 3.7 Data freshness

- Streamlit reads `results/*.csv` and `*.json` baked into the deployment
- To update content (new scorecard, new strategy variant): re-run the Python pipeline locally → commit → redeploy
- No live data pipeline in v1 (no live trading yet)

### 3.8 Deliverables

```
src/bursahack_ui/
  __init__.py
  app.py                    # entry point + auth
  pages/
    1_📈_Performance.py
    2_🔬_Methodology.py
    3_⚖️_Scorecard.py
  components/
    plots.py                # plotly chart functions
    cards.py                # stat cards, scorecard rows
    auth.py                 # auth wrapper
  data/
    loader.py               # cached CSV loaders
  theme/
    custom.css              # injected CSS
.streamlit/
  config.toml               # theme
Dockerfile                  # for Railway deploy
.dockerignore
```

### 3.9 Build sequence

| Phase | Work | Est. time |
|---|---|---|
| 1 | Project skeleton, theme config, custom CSS, sidebar nav polish | 1 hr |
| 2 | Auth wrapper (Cloudflare Access in front, or session-state password) | 30 min |
| 3 | Data loaders + Plotly chart components | 1.5 hr |
| 4 | Page 1 Overview (stat cards, sparkline, CTAs) | 30 min |
| 5 | Page 2 Performance (capital + window toggles, equity curve, drawdown, year-bar drill-in) | 1.5 hr |
| 6 | Page 3 Methodology (process diagram, fold visualizer, leaderboard) | 1 hr |
| 7 | Page 4 Scorecard (12-gate display with tooltips, kill triggers, risks) | 1 hr |
| 8 | Mobile responsive polish, Dockerfile, deploy to Railway, CF Access setup | 1 hr |

**Total: ~8 hours**. Either one long session or split across two.

---

## 4. Stage 2 — Next.js V1 (Later)

### 4.1 When to start

Trigger: Streamlit V1 has been used by ≥ 3 real investors for ≥ 2 weeks, and:
- Content is settled (no more rewrites of methodology section)
- UX patterns are clear (which charts get used, which get ignored)
- A specific feature is being held back by Streamlit's constraints

### 4.2 What carries over

| From Stage 1 | To Stage 2 |
|---|---|
| All page content (Overview, Methodology, Risks text) | Markdown imports, same content |
| Chart designs (equity, drawdown, heatmap layouts) | Re-implemented in Plotly.js / Recharts but same visual |
| Color palette, typography | Same Tailwind config |
| Data files (CSVs, JSONs) | Read by FastAPI backend |
| Auth model (allowlist) | NextAuth magic-link OR keep CF Access |

### 4.3 What's new in Stage 2

- **Real auth flow**: NextAuth with database-backed sessions
- **Polish UI**: shadcn/ui components, smooth Framer Motion transitions
- **True mobile-first**: native-app-feeling on phones
- **Drill-down modals**: click year-bar → modal slides in with month detail (Streamlit can fake this but not smoothly)
- **Custom investor profiles**: optionally, per-investor view (e.g. "you'd have started at RM 250k, here's your path")
- **Eventually**: live data via WebSocket (when strategy is paper-trading or live)

### 4.4 Stack

| Layer | Stack |
|---|---|
| Frontend | Next.js (App Router) + Tailwind + shadcn/ui + Plotly.js |
| Backend | FastAPI wrapping `bursahack.*` modules |
| Auth | NextAuth.js + magic-link via Resend |
| DB (sessions) | Vercel Postgres or Railway Postgres |
| Hosting | Cloudflare Pages (frontend) + Railway (backend) |
| Monitoring | Sentry, basic |

### 4.5 Migration path

1. Build FastAPI backend exposing endpoints that Streamlit already needs (`/equity?capital=&window=`, `/scorecard`, etc.). This is the **same backend the Next.js app will use**.
2. Streamlit calls the FastAPI backend instead of reading CSVs directly. (Optional intermediate step — keeps both apps coherent.)
3. Build Next.js frontend that calls the same FastAPI endpoints.
4. Switch DNS / share new Next.js URL with investors.
5. Retire the Streamlit app, OR keep it as the operator's research tool (Track A use case).

### 4.6 Estimated build (rough)

| Phase | Work | Est. time |
|---|---|---|
| 1 | FastAPI backend with endpoints + tests | 4 hr |
| 2 | Next.js scaffold + Tailwind + shadcn + auth | 3 hr |
| 3 | Page 1 (Overview) + Page 2 (Performance) | 5 hr |
| 4 | Page 3 (Methodology) + Page 4 (Scorecard) | 4 hr |
| 5 | Mobile polish, animations, branding, deploy | 2 hr |

**Total: ~18 hours across 3-4 sessions.**

---

## 5. Open Questions (to settle before Stage 1 build)

These need answers before the next-session build. The default in [brackets] is what I'll go with unless told otherwise.

1. **Domain**: subdomain of fatfyre.com (e.g. `bursamomentum.fatfyre.com`) vs default `*.up.railway.app`?
   - [default: `*.up.railway.app` initially, upgrade to fatfyre subdomain after Streamlit is live]

2. **Brand name on the navy bar**: "FatFyre Research" or other?
   - [default: "FatFyre Research" to match the PDF proposal]

3. **Auth**: Cloudflare Access magic-link, simple shared password, or no auth (private URL only)?
   - [default: Cloudflare Access if you confirm you want it; otherwise shared password]

4. **Content scope**: feature the rotation winner only, OR include the Clenow #9 alternative that scored better OOS?
   - [default: rotation winner as the headline, mention Clenow in methodology/search-results section]

5. **Mobile target**: must look perfect on phone, or "tolerable on phone, designed for desktop"?
   - [default: tolerable on phone, designed for desktop — Streamlit's responsive defaults aren't perfect]

6. **Are the answers you gave for Track B previously (when I asked) still binding?**
   - You answered: share with specific investors / family before strategy is locked, just B (skip Track A)
   - That's compatible with this plan.

---

## 6. Iron rules for the build

- **Read-only**. No parameter sliders in the user-facing app. (If you want a research-tool with sliders, that's Track A — separate project.)
- **Numbers reconcile to artifacts**. Every metric shown is sourced from a file in `results/`. No hand-tweaks.
- **Research-stage badge prominent**. Until a strategy is tier A/B per the framework, the badge stays.
- **No live-trading claims**. The app shows backtest + holdout results, not live performance.
- **Static between data refreshes**. App is a deployed snapshot, not a live system. Refresh = redeploy.

---

## 7. Version log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-05-18 | Initial UI plan. Two-stage rollout: Streamlit then Next.js. Drafted before any code; build begins next session. |
