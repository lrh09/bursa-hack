"""Self-contained HTML tearsheet assembly for the options toolkit.

This module turns an *analysis context* (``ctx``) -- the bag of numbers, tables
and rendered charts produced by the pure-analytics + viz layers -- into one
portable HTML file with every chart embedded as a base64 ``data:`` URI. Zero
CDN, zero external JS, zero network. It mirrors the look + portability of
``web/public/crypto_deflated_report.html`` (dark desk theme, KPI grid, plain
tables).

Two public entry points, by design:

  * ``build_html_report(ctx, out_path, mode="book") -> str``
      The frozen, contract-level renderer. Pure: it consumes a ready ``ctx``
      dict and writes the HTML file. jinja2 is the primary templating engine
      (imported lazily); when jinja2 is absent the renderer falls back to
      ``string.Template`` so the report still builds on a bare interpreter.

  * ``build_report(book, account, out_path=None, ...) -> str``
      The orchestrator named in this module's build assignment. It runs the
      analytics + chart pipeline (``build_ctx``) over a ``Book`` + ``Account``
      and then calls ``build_html_report``. Sibling analytics/viz modules are
      imported lazily so this module composes even while they are still being
      built in parallel -- any missing piece degrades to an omitted section,
      never a crash.

Report section order (deterministic, per the build contract):
  1. Header        -- underlyings, spot, as-of, NLV, assumptions (r, q, vol, model, seed)
  2. Book summary  -- net dollar-greeks, beta-weighted delta, income posture, P(profit)
  3. Position cards-- legs, entry/mark, greeks, BE, max P/L, R:R, POP, payoff thumbnail
  4. Greeks dash   -- greeks_vs_spot small-multiples
  5. Scenario      -- scenario grid + tornado + spot x vol / spot x time surfaces
  6. Probability/MC-- probability cone + MC histogram + VaR/CVaR table (with floor note)
  6b.Combination   -- subset comparison table + per-candidate decision cards
                      (scorecard KPIs, liquidation point/%, P(liquidation), verdicts)
  7. Risk & margin -- delta ladder + expiration timeline (+ margin waterfall in v2)
  8. Strategy      -- posture, empty-shelf, roll forward-delta framing, IV-rank gate
  9. Catalysts     -- earnings / ex-div in window + early-assignment flags
 10. Appendix      -- full-res charts + full assumptions block

Self-contained guarantees (locked by the report test in the contract):
  * No ``http(s)://`` anywhere in the output.
  * Every ``<img>`` is a ``data:image/png;base64,...`` URI.
  * Builds with jinja2 absent (``string.Template`` fallback).
  * Deterministic: same ``ctx`` -> byte-identical HTML.

References:
  * web/public/crypto_deflated_report.html  -- portability + visual template
  * BUILD CONTRACT bursahack.options FINAL, sections 3 (viz/report) + report flow
"""
from __future__ import annotations

import base64
import html as _html
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from string import Template
from typing import Any, Mapping, Sequence

from bursahack.paths import REPO_ROOT

# --- canonical options dirs (inlined per the contract; do not touch paths.py) ---
WEB_PUBLIC = REPO_ROOT / "web" / "public"
OPTIONS_RESULTS = REPO_ROOT / "results" / "options"


# ============================================================================
# Context schema
# ============================================================================
#
# ``ctx`` is a plain nested dict so that any producer (build_report here, the
# scripts/run_options_analysis.py CLI, or a notebook) can assemble it. Every
# consumer reads it through ``.get(...)`` with defaults, so a partially built
# pipeline still yields a valid (smaller) report. ReportContext below documents
# the canonical shape and offers a typed builder, but the renderer accepts a
# bare dict too.
#
# Top-level keys (all optional unless noted):
#   meta:        {title, generated, asof, mode, underlyings:[...], spot:{sym:px},
#                 nlv, assumptions:{r,q,vol_source,model,seed,measure,mu,mc_paths}}
#   summary:     {dollar_greeks:{delta,...}, beta_weighted_delta, income_posture:{...},
#                 prob_profit, kpis:[{label,value,cls}]}
#   positions:   [{name, classification, underlying, legs:[{...}], entry, mark,
#                  greeks:{...}, breakevens:[...], max_profit, max_loss, rr, pop,
#                  thumb (base64 data-uri)}]
#   charts:      {chart_id: data-uri}            # base64 PNGs from render_all_charts
#   scenario:    {grid_rows:[{spot,iv,day,pnl,pct,...}], cols:[...]}
#   probability: {var_cvar:[{alpha,var,cvar,floor,flag}], notes:[...]}
#   strategy:    {posture, advice:[...], iv_rank:{...}}
#   catalysts:   {earnings:[...], dividends:[...], assignment:[...]}
#   reconcile:   [{name, passed, gap, gap_in_se}]
#   warnings:    [str]                            # e.g. book-load validation warnings
#
# ``charts`` chart_ids the renderer looks for (each falls through gracefully):
#   payoff, greeks_vs_spot, pnl_surface_spot_vol, pnl_surface_spot_time,
#   probability_cone, mc_histogram, delta_ladder, scenario_tornado,
#   expiration_timeline, vol_smile, vol_term_structure, iv_vs_hv, dashboard


@dataclass
class ReportContext:
    """Typed, optional helper for assembling a report ``ctx``.

    Not required -- ``build_html_report`` accepts a plain dict -- but handy when
    building the context programmatically. ``as_dict()`` returns the nested dict
    the renderer consumes.
    """

    meta: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    charts: dict[str, str] = field(default_factory=dict)
    scenario: dict[str, Any] = field(default_factory=dict)
    probability: dict[str, Any] = field(default_factory=dict)
    strategy: dict[str, Any] = field(default_factory=dict)
    catalysts: dict[str, Any] = field(default_factory=dict)
    reconcile: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "summary": self.summary,
            "positions": self.positions,
            "charts": self.charts,
            "scenario": self.scenario,
            "probability": self.probability,
            "strategy": self.strategy,
            "catalysts": self.catalysts,
            "reconcile": self.reconcile,
            "warnings": self.warnings,
        }


# ============================================================================
# Small formatting helpers (pure, deterministic, no I/O)
# ============================================================================

def _esc(x: Any) -> str:
    """HTML-escape a value (None -> empty)."""
    if x is None:
        return ""
    return _html.escape(str(x), quote=True)


def _fmt_num(x: Any, dp: int = 2) -> str:
    """Round-only-for-display number formatter (math is never rounded, L3)."""
    if x is None:
        return "—"
    if isinstance(x, str):
        return _esc(x)
    try:
        v = float(x)
    except (TypeError, ValueError):
        return _esc(x)
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return "∞" if v == float("inf") else ("−∞" if v == float("-inf") else "—")
    return f"{v:,.{dp}f}"


def _fmt_money(x: Any, dp: int = 0) -> str:
    if x is None:
        return "—"
    if isinstance(x, str):
        return _esc(x)
    try:
        v = float(x)
    except (TypeError, ValueError):
        return _esc(x)
    sign = "−" if v < 0 else ""
    return f"{sign}${abs(v):,.{dp}f}"


def _fmt_pct(x: Any, dp: int = 1) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return _esc(x)
    return f"{v * 100:,.{dp}f}%"


def _signed_cls(x: Any) -> str:
    """CSS class for a signed number (good/bad/neutral)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "good"
    if v < 0:
        return "bad"
    return ""


def _as_iso(x: Any) -> str:
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    return _esc(x)


def fig_to_base64(fig: Any, dpi: int = 110) -> str:
    """Render a matplotlib Figure to a base64 ``data:image/png`` URI and close it.

    Lazy on matplotlib so this module imports on a bare interpreter. Used when a
    producer hands the renderer live Figures instead of pre-rendered URIs.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    try:
        import matplotlib.pyplot as plt  # lazy

        plt.close(fig)
    except Exception:
        pass
    return _bytes_to_data_uri(buf.getvalue())


def png_path_to_base64(path: str | Path) -> str:
    """Read a PNG file and return a base64 ``data:`` URI (self-contained embed)."""
    data = Path(path).read_bytes()
    return _bytes_to_data_uri(data)


def _bytes_to_data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _coerce_chart(value: Any) -> str | None:
    """Normalise a chart entry to a base64 data-URI string.

    Accepts: an already-formed ``data:`` URI, a ``(png_path, base64)`` tuple as
    produced by ``viz.charts.render_all_charts``, a bare PNG path, a Figure, or
    raw PNG bytes. Returns None if it cannot resolve to an embeddable image.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith("data:image"):
            return value
        # treat as a filesystem path to a PNG
        try:
            p = Path(value)
            if p.exists() and p.suffix.lower() == ".png":
                return png_path_to_base64(p)
        except OSError:
            return None
        return None
    if isinstance(value, (tuple, list)) and value:
        # render_all_charts shape: (png_path, base64_uri) -- prefer the URI
        for item in reversed(value):
            uri = _coerce_chart(item)
            if uri:
                return uri
        return None
    if isinstance(value, (bytes, bytearray)):
        return _bytes_to_data_uri(bytes(value))
    # matplotlib Figure?
    if hasattr(value, "savefig"):
        try:
            return fig_to_base64(value)
        except Exception:
            return None
    return None


# ============================================================================
# Stylesheet (mirrors crypto_deflated_report.html; self-contained, no CDN)
# ============================================================================

_CSS = """
  :root { --bg:#0d1117; --card:#161b22; --bd:#30363d; --tx:#c9d1d9; --mut:#8b949e;
          --good:#3fb950; --warn:#d29922; --bad:#f85149; --acc:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--tx); font:15px/1.6 -apple-system,
         BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:min(1000px,100%); margin:0 auto; padding:32px 20px 80px; }
  h1 { font-size:26px; margin:0 0 4px; }
  h2 { font-size:18px; margin:34px 0 12px; border-bottom:1px solid var(--bd); padding-bottom:6px; }
  h3 { font-size:15px; margin:18px 0 8px; color:var(--acc); }
  .sub { color:var(--mut); margin:0 0 24px; }
  .card { background:var(--card); border:1px solid var(--bd); border-radius:10px;
          padding:18px 20px; margin:16px 0; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
  .kpi { background:var(--card); border:1px solid var(--bd); border-radius:10px; padding:14px 16px; }
  .kpi .v { font-size:24px; font-weight:700; }
  .kpi .l { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .good{color:var(--good)} .warn{color:var(--warn)} .bad{color:var(--bad)} .acc{color:var(--acc)}
  .mut{color:var(--mut)}
  table { width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }
  th,td { text-align:right; padding:6px 10px; border-bottom:1px solid var(--bd); }
  th:first-child,td:first-child { text-align:left; font-family:ui-monospace,monospace; }
  th { color:var(--mut); font-weight:600; }
  code { background:#21262d; padding:1px 6px; border-radius:5px; font-size:13px; }
  ul { padding-left:20px; } li { margin:6px 0; }
  .caveat { border-left:3px solid var(--warn); }
  .banner { border-left:3px solid var(--bad); background:#1d1418; }
  .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr)); gap:16px; }
  .tablewrap { overflow-x:auto; -webkit-overflow-scrolling:touch; }
  .chart { background:var(--card); border:1px solid var(--bd); border-radius:10px; padding:12px; margin:12px 0; }
  .chart img { width:100%; height:auto; display:block; border-radius:6px; }
  .chart .cap { color:var(--mut); font-size:12px; margin-top:6px; text-align:center; }
  .pos { background:var(--card); border:1px solid var(--bd); border-radius:10px;
         padding:16px 18px; margin:14px 0; }
  .pos h3 { margin-top:0; }
  .pill { display:inline-block; font-size:11px; padding:2px 8px; border-radius:10px;
          background:#21262d; color:var(--mut); margin-left:8px; vertical-align:middle; }
  .badge { display:inline-block; font-size:12px; padding:3px 9px; border-radius:6px; margin:2px; }
  .badge.ok { background:#10301a; color:var(--good); }
  .badge.fail { background:#3a1518; color:var(--bad); }
  .thumb { max-width:360px; }
  .foot { color:var(--mut); font-size:12px; margin-top:40px; border-top:1px solid var(--bd); padding-top:16px; }
  .disc { border-left:3px solid var(--acc); background:#0f1620; }
  @media (max-width:640px) {
    .wrap { padding:20px 14px 56px; }
    h1 { font-size:22px; }
    .sub { font-size:11px; word-break:break-word; }
    /* stack two-up chart rows on phone (380px track floor would otherwise
       overflow a ~350px content column and force sideways scroll) */
    .grid2 { grid-template-columns:1fr; }
    /* keep KPIs at two-up on phone to trade a little width for less scrolling */
    .kpis { grid-template-columns:repeat(2,1fr); }
  }
"""


# ============================================================================
# Section renderers (each returns an HTML fragment; all defensive on ctx)
# ============================================================================

# Mark-value caveat — this book carries entry_price=0 on every leg, so any figure
# derived from (mark − entry) is the GROSS MARK VALUE, not realized P&L. Several
# headline numbers (per-position "Mark Value", scorecard EV/POP, the comparison
# table's pnl_base) are therefore mark value / gross, NOT profit-and-loss. We
# surface this prominently rather than silently calling mark value "P&L"/"EV".
_MARK_VALUE_NOTE = (
    "Entry prices are 0 for every leg in this book, so figures marked "
    "<strong>*</strong> (per-position <strong>Mark Value*</strong> and "
    "<strong>Max loss*</strong>, the comparison table's <strong>Mark now*</strong> "
    "(<code>pnl_base</code>) and <strong>max loss*</strong>, and the scorecard "
    "<strong>EV* / POP* / VaR 95* / CVaR 95* / Cap. efficiency* / "
    "Reward:risk*</strong>) are <strong>gross mark value, not realized P&amp;L</strong>. "
    "Because no premium was paid, the long-debit subsets also show "
    "max loss / VaR / CVaR collapsing toward 0 and Reward:risk toward ∞, so the "
    "cross-subset risk ranking is <strong>not apples-to-apples</strong> until real "
    "fills are loaded. Note also that the comparison table's <strong>Mark now*</strong> "
    "is the value at <em>today&#39;s</em> spot (dt=0), whereas the per-subset "
    "<strong>EV* (RN,30d)</strong> is the risk-neutral <em>expected</em> value at "
    "the 30-day horizon — they are different quantities, not a contradiction. Treat all "
    "starred figures as exposure sizing, not profit."
)


def _kpi(label: str, value: str, cls: str = "") -> str:
    return (
        f'<div class="kpi"><div class="v {cls}">{value}</div>'
        f'<div class="l">{_esc(label)}</div></div>'
    )


def _chart_block(uri: str | None, caption: str = "") -> str:
    uri = _coerce_chart(uri)
    if not uri:
        return ""
    cap = f'<div class="cap">{_esc(caption)}</div>' if caption else ""
    return f'<div class="chart"><img alt="{_esc(caption)}" src="{uri}">{cap}</div>'


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]],
           cell_cls: Mapping[tuple[int, int], str] | None = None) -> str:
    if not rows:
        return ""
    cell_cls = cell_cls or {}
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body_rows = []
    for ri, row in enumerate(rows):
        cells = []
        for ci, cell in enumerate(row):
            cls = cell_cls.get((ri, ci), "")
            cls_attr = f' class="{cls}"' if cls else ""
            cells.append(f"<td{cls_attr}>{cell}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><tr>{head}</tr>{''.join(body_rows)}</table>"


# --- (1) Header ------------------------------------------------------------

def _section_header(ctx: Mapping[str, Any]) -> str:
    meta = ctx.get("meta", {})
    title = _esc(meta.get("title") or "Options Book — Desk Tearsheet")
    generated = _as_iso(meta.get("generated") or datetime.now().replace(microsecond=0))
    asof = _as_iso(meta.get("asof") or "")
    unders = meta.get("underlyings") or []
    spot = meta.get("spot") or {}
    a = meta.get("assumptions") or {}

    sub_bits = []
    if unders:
        sub_bits.append(", ".join(_esc(u) for u in unders))
    if asof:
        sub_bits.append(f"as-of {asof}")
    sub_bits.append(f"generated {generated}")
    sub = " · ".join(sub_bits)

    kpis = []
    for sym in unders:
        px = spot.get(sym) if isinstance(spot, Mapping) else None
        if px is not None:
            kpis.append(_kpi(f"{sym} spot", _fmt_num(px, 2), "acc"))
    if meta.get("nlv") is not None:
        kpis.append(_kpi("Net Liq (NLV)", _fmt_money(meta.get("nlv")), ""))
    kpi_html = f'<div class="kpis">{"".join(kpis)}</div>' if kpis else ""

    rows = []
    label_map = [
        ("r (risk-free)", "r", _fmt_pct),
        ("q (div yield)", "q", _fmt_pct),
        ("vol source", "vol_source", _esc),
        ("model", "model", _esc),
        ("measure", "measure", _esc),
        ("μ (real-world drift)", "mu", _fmt_pct),
        ("MC paths", "mc_paths", lambda v: _fmt_num(v, 0)),
        ("MC seed", "seed", _esc),
    ]
    for label, key, fmt in label_map:
        if key in a and a.get(key) is not None:
            rows.append([_esc(label), fmt(a.get(key))])
    assumptions = (
        '<div class="card disc"><strong>Assumptions</strong>'
        + _table(["parameter", "value"], rows)
        + "</div>"
        if rows else ""
    )

    return (
        f"<h1>{title}</h1>"
        f'<p class="sub">{sub}</p>'
        f"{kpi_html}"
        f"{assumptions}"
    )


# --- warnings (validation) -------------------------------------------------

def _section_warnings(ctx: Mapping[str, Any]) -> str:
    warns = ctx.get("warnings") or []
    if not warns:
        return ""
    items = "".join(f"<li>{_esc(w)}</li>" for w in warns)
    return f'<div class="card caveat"><strong>Book-load warnings</strong><ul>{items}</ul></div>'


# --- (2) Book summary ------------------------------------------------------

def _section_summary(ctx: Mapping[str, Any]) -> str:
    s = ctx.get("summary", {})
    if not s:
        return ""
    out = ["<h2>Book summary</h2>"]

    dg = s.get("dollar_greeks") or {}
    kpis = []
    # explicit KPI list wins if provided
    for k in s.get("kpis", []) or []:
        kpis.append(_kpi(k.get("label", ""), _esc(k.get("value", "")), k.get("cls", "")))
    if not kpis and dg:
        greek_kpis = [
            ("$ Delta", dg.get("dollar_delta"), _fmt_money),
            ("$ Gamma /1%", dg.get("dollar_gamma_1pct"), _fmt_money),
            ("$ Vega", dg.get("dollar_vega"), _fmt_money),
            ("$ Theta /day", dg.get("dollar_theta_day"), _fmt_money),
            ("$ Rho /1%", dg.get("dollar_rho_1pct"), _fmt_money),
            ("Delta (shares)", dg.get("delta_sh"), lambda v: _fmt_num(v, 1)),
        ]
        for label, val, fmt in greek_kpis:
            if val is not None:
                kpis.append(_kpi(label, fmt(val), _signed_cls(val)))
    if s.get("beta_weighted_delta") is not None:
        bwd = s["beta_weighted_delta"]
        bwd_v = bwd.get("total") if isinstance(bwd, Mapping) else bwd
        kpis.append(_kpi("β-wtd Δ (SPY)", _fmt_num(bwd_v, 1), _signed_cls(bwd_v)))
    if s.get("prob_profit") is not None:
        kpis.append(_kpi("P(profit)", _fmt_pct(s["prob_profit"]), ""))
    if kpis:
        out.append(f'<div class="kpis">{"".join(kpis)}</div>')

    posture = s.get("income_posture") or {}
    if posture:
        verdict = _esc(posture.get("posture") or posture.get("recommended_side") or "")
        rec = _esc(posture.get("recommended_side") or "")
        anti = posture.get("anti_recommendation")
        anti_reason = posture.get("reason") or posture.get("anti_reason")
        lines = []
        if verdict:
            lines.append(f"<strong>Posture:</strong> {verdict}")
        if rec and rec != verdict:
            lines.append(f"<strong>Recommended:</strong> {rec}")
        if anti:
            warn = f" — {_esc(anti_reason)}" if anti_reason else ""
            lines.append(f'<span class="bad"><strong>Avoid:</strong> {_esc(anti)}{warn}</span>')
        if lines:
            out.append('<div class="card">' + "<br>".join(lines) + "</div>")

    return "".join(out) if len(out) > 1 else ""


# --- (3) Position cards ----------------------------------------------------

def _leg_label(leg: Mapping[str, Any]) -> str:
    qty = leg.get("qty", 0)
    try:
        qf = float(qty)
        side = "+" if qf > 0 else ("−" if qf < 0 else "")
        qstr = f"{side}{abs(qf):g}"
    except (TypeError, ValueError):
        qstr = _esc(qty)
    right = _esc(leg.get("right", ""))
    strike = leg.get("strike")
    strike_str = _fmt_num(strike, 2) if strike not in (None, 0, 0.0) else ""
    exp = _as_iso(leg.get("expiry") or "")
    entry = leg.get("entry_price")
    entry_str = f" @ {_fmt_num(entry, 2)}" if entry not in (None,) else ""
    parts = [p for p in [qstr, strike_str, right, exp] if p]
    return " ".join(parts) + entry_str


def _is_call_leg(leg: Mapping[str, Any]) -> bool:
    """True if the leg is a CALL, tolerant of right encodings ('C'/'CALL'/…)."""
    r = str(leg.get("right") or "").strip().upper()
    return r in ("C", "CALL") or r.endswith(".CALL")


def _has_open_long_call_uptail(p: Mapping[str, Any]) -> bool:
    """Does this position have a genuinely UNBOUNDED up-tail?

    Display-layer detector (issue #2). The engine's ``risk.defined_risk`` already
    reports ``max_profit='unbounded'`` for a net long-call up-tail, which
    ``structure.economics`` maps to ``max_profit=None``; but the CLI's report ctx
    may not forward that nuance, so the renderer also infers it from the data it
    actually receives. A position is up-unbounded when ANY of:

      * an explicit producer flag says so (``max_profit_unbounded`` /
        ``unbounded_side`` in {'up','both'}); or
      * ``max_profit`` is the literal string ``'unbounded'``; or
      * the legs net to a POSITIVE call quantity at the highest call strike
        (long calls not capped by a further-out short call) — the canonical
        long-call / ratio up-tail. Spreads / condors cap the top strike with a
        short call (net qty ≤ 0 there) and are correctly treated as bounded.
    """
    # explicit producer signals win
    if p.get("max_profit_unbounded"):
        return True
    if str(p.get("unbounded_side") or "").lower() in ("up", "both"):
        return True
    if isinstance(p.get("max_profit"), str) and \
            p.get("max_profit").strip().lower() == "unbounded":
        return True
    # infer from legs: net call quantity at the highest call strike
    legs = p.get("legs") or []
    call_legs = [lg for lg in legs if _is_call_leg(lg)]
    if not call_legs:
        return False
    top_strike: float | None = None
    for lg in call_legs:
        try:
            k = float(lg.get("strike"))
        except (TypeError, ValueError):
            continue
        if top_strike is None or k > top_strike:
            top_strike = k
    if top_strike is None:
        return False
    net_top_qty = 0.0
    for lg in call_legs:
        try:
            k = float(lg.get("strike"))
            q = float(lg.get("qty"))
        except (TypeError, ValueError):
            continue
        if k == top_strike:
            net_top_qty += q
    # a positive net long position in the highest call ⇒ open up-tail
    return net_top_qty > 1e-9


def _entry_is_zero(p: Mapping[str, Any]) -> bool:
    """True when the position's net entry/premium is zero (entry_price=0 book).

    Reads the position-level ``entry`` (net premium) when present; otherwise falls
    back to inspecting the legs (all ``entry_price`` 0/None ⇒ entry 0). Used to gate
    the breakeven-plateau suppression (issue #3) and the entry-collapse note.
    """
    entry = p.get("entry")
    if isinstance(entry, (int, float)):
        return abs(float(entry)) < 1e-9
    legs = p.get("legs") or []
    if not legs:
        return False
    saw_price = False
    for lg in legs:
        ep = lg.get("entry_price")
        if ep is None:
            continue
        try:
            if abs(float(ep)) >= 1e-9:
                return False
            saw_price = True
        except (TypeError, ValueError):
            return False
    return saw_price


def _leg_strikes(p: Mapping[str, Any]) -> list[float]:
    out: list[float] = []
    for lg in p.get("legs") or []:
        try:
            k = float(lg.get("strike"))
        except (TypeError, ValueError):
            continue
        if k > 0:
            out.append(k)
    return out


def _display_breakevens(p: Mapping[str, Any]) -> list[float]:
    """Breakevens to display, with the entry=0 plateau artifacts removed (issue #3).

    The breakeven finder works on net P&L over a kink grid. When net_cost==0 the
    payoff sits flat at zero across a whole region (e.g. all S below a long call's
    strike), so the finder emits spurious "crossings" on that zero-plateau (e.g.
    110.00 and 219.78 for a 220C whose only genuine breakeven is the strike 220).

    Display-layer fix: when entry==0, keep only breakevens that coincide with an
    actual leg strike — the genuine kink where P&L leaves the zero plateau. If that
    filter would drop everything (defensive — no strike match), fall back to the
    raw list so we never blank a position. When entry≠0 the breakevens are real;
    pass them through untouched.
    """
    raw = p.get("breakevens")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        vals = [float(b) for b in raw if isinstance(b, (int, float))]
    elif isinstance(raw, (int, float)):
        vals = [float(raw)]
    else:
        return []
    if not vals:
        return []
    if not _entry_is_zero(p):
        return vals
    strikes = _leg_strikes(p)
    if not strikes:
        return vals
    tol = 1e-6
    genuine = [v for v in vals if any(abs(v - k) <= max(tol, abs(k) * 1e-6)
                                      for k in strikes)]
    return genuine or vals


def _section_positions(ctx: Mapping[str, Any]) -> str:
    positions = ctx.get("positions") or []
    if not positions:
        return ""
    out = ["<h2>Positions</h2>"]
    out.append(f'<div class="card caveat"><strong>Mark value, not P&amp;L.</strong> '
               f'{_MARK_VALUE_NOTE}</div>')
    for p in positions:
        name = _esc(p.get("name") or p.get("underlying") or "Position")
        cls = p.get("classification") or p.get("name_class")
        pill = f'<span class="pill">{_esc(cls)}</span>' if cls else ""
        card = [f'<div class="pos"><h3>{name}{pill}</h3>']

        legs = p.get("legs") or []
        if legs:
            card.append("<ul>")
            for leg in legs:
                card.append(f"<li><code>{_leg_label(leg)}</code></li>")
            card.append("</ul>")

        # economics KPIs. "entry" is net_premium from entry_price; with this
        # book's entry_price=0 it is ~0, and "mark" (if present) is gross mark
        # value, not realized P&L. Mark Value / Max loss carry a * tying back to
        # _MARK_VALUE_NOTE — at entry=0 the max-loss collapses toward 0 (no premium
        # paid) and R:R toward ∞, so neither is apples-to-apples until real fills
        # are loaded.
        ekpis = []
        # Issue #2 — genuinely open up-tail (net long calls / long-call-dominated
        # ratios): the upside is UNBOUNDED. The engine evaluates the payoff at a
        # far-up grid point (K×3+1), so a finite "Max profit" here is a grid
        # artifact, not a real ceiling. Show "unbounded (∞)" instead of the dollar
        # figure whenever the up-tail is open — detected from the legs / producer
        # flags by _has_open_long_call_uptail, independent of whether the producer
        # forwarded the 'unbounded' sentinel.
        up_unbounded = _has_open_long_call_uptail(p)
        kvs = [
            ("Net premium (entry)", p.get("entry"), _fmt_money),
            ("Mark Value*", p.get("mark"), _fmt_money),
            ("Max profit", p.get("max_profit"), _fmt_money),
            ("Max loss*", p.get("max_loss"), _fmt_money),
            ("R:R", p.get("rr"), lambda v: _fmt_num(v, 2)),
            ("POP", p.get("pop"), _fmt_pct),
        ]
        for label, val, fmt in kvs:
            if label == "Max profit" and up_unbounded:
                # open up-tail wins over any finite (grid-cap or None) value
                ekpis.append(_kpi("Max profit", "unbounded (∞)", "good"))
            elif val is not None:
                ekpis.append(_kpi(label, fmt(val), ""))
        be = _display_breakevens(p)
        if be:
            be_txt = ", ".join(_fmt_num(b, 2) for b in be)
            ekpis.append(_kpi("Breakeven", be_txt, "acc"))
        if ekpis:
            card.append(f'<div class="kpis">{"".join(ekpis)}</div>')
        # Issue #3 — at entry=0 the breakeven finder collapses to the strike(s);
        # annotate so a reader knows the lone strike is the genuine BE, not a bug.
        if be and _entry_is_zero(p):
            card.append('<p class="sub">entry = 0 → breakevens collapse to the '
                        'strike(s).</p>')

        # per-leg / aggregate greeks
        g = p.get("greeks") or {}
        if g:
            order = [
                ("Δ delta", "delta", 4),
                ("Γ gamma", "gamma", 5),
                ("Θ theta/day", "theta_day", 4),
                ("ν vega", "vega", 4),
                ("ρ rho", "rho", 4),
            ]
            rows = [[_esc(lbl), _fmt_num(g.get(key), dp)] for lbl, key, dp in order if g.get(key) is not None]
            if rows:
                # These are POSITION-level greeks (per-share greek × qty × mult):
                # delta is share-equivalent exposure; theta/vega/rho are position
                # dollars (theta $/day, vega $/vol-pt, rho $/1%). NOT per-share — the
                # header spells out the units so the table is never misread as
                # "per share".
                card.append("<h3 class='mut'>Greeks (position: delta sh-eq, "
                            "theta $/day, vega $/vol-pt)</h3>")
                card.append(_table(["greek", "value"], rows))

        # payoff thumbnail (base64)
        thumb = _coerce_chart(p.get("thumb") or p.get("payoff"))
        if thumb:
            card.append(
                f'<div class="chart thumb"><img alt="payoff" src="{thumb}">'
                f'<div class="cap">expiry P&amp;L</div></div>'
            )

        card.append("</div>")
        out.append("".join(card))
    return "".join(out)


# --- generic charts section helper -----------------------------------------

def _charts(ctx: Mapping[str, Any]) -> Mapping[str, Any]:
    return ctx.get("charts") or {}


# --- (4) Greeks dashboard --------------------------------------------------

def _section_greeks_dash(ctx: Mapping[str, Any]) -> str:
    c = _charts(ctx)
    blk = _chart_block(c.get("greeks_vs_spot"), "Greeks vs spot")
    if not blk:
        return ""
    return "<h2>Greeks dashboard</h2>" + blk


# --- (5) Scenario ----------------------------------------------------------

def _section_scenario(ctx: Mapping[str, Any]) -> str:
    c = _charts(ctx)
    sc = ctx.get("scenario") or {}
    out = []

    grid_rows = sc.get("grid_rows") or []
    table_html = ""
    if grid_rows:
        cols = sc.get("cols") or ["spot", "iv", "day", "pnl", "pct", "delta", "gamma", "theta", "vega"]
        cols = [c2 for c2 in cols if any(c2 in r for r in grid_rows)]
        rows = []
        cell_cls: dict[tuple[int, int], str] = {}
        for ri, r in enumerate(grid_rows):
            row = []
            for ci, col in enumerate(cols):
                v = r.get(col)
                if col in ("pnl",):
                    row.append(_fmt_money(v))
                    cell_cls[(ri, ci)] = _signed_cls(v)
                elif col in ("pct",):
                    row.append(_fmt_pct(v) if isinstance(v, (int, float)) else _esc(v))
                    cell_cls[(ri, ci)] = _signed_cls(v)
                elif col in ("spot", "delta", "gamma", "theta", "vega", "iv"):
                    row.append(_fmt_num(v, 3 if col in ("gamma",) else 2))
                else:
                    row.append(_esc(v))
            rows.append(row)
        table_html = _table(cols, rows, cell_cls)

    tornado = _chart_block(c.get("scenario_tornado"), "Scenario tornado")
    surf_v = _chart_block(c.get("pnl_surface_spot_vol"), "P&L surface — spot × vol")
    surf_t = _chart_block(c.get("pnl_surface_spot_time"), "P&L surface — spot × time")

    if not (table_html or tornado or surf_v or surf_t):
        return ""
    out.append("<h2>Scenario analysis</h2>")
    if table_html:
        # the scenario grid runs up to ~9 numeric columns — wrap it so it scrolls
        # inside the card on a phone instead of forcing the page sideways (#5).
        out.append('<div class="card"><div class="tablewrap">' + table_html
                   + "</div></div>")
    if tornado:
        out.append(tornado)
    if surf_v or surf_t:
        out.append(f'<div class="grid2">{surf_v}{surf_t}</div>')
    return "".join(out)


# --- (6) Probability / MC --------------------------------------------------

def _section_probability(ctx: Mapping[str, Any]) -> str:
    c = _charts(ctx)
    prob = ctx.get("probability") or {}
    out = []

    cone = _chart_block(c.get("probability_cone"), "Probability cone")
    hist = _chart_block(c.get("mc_histogram"), "Monte-Carlo P&L distribution")

    var_rows = prob.get("var_cvar") or []
    var_table = ""
    if var_rows:
        rows = []
        cell_cls: dict[tuple[int, int], str] = {}
        any_floor = False
        for ri, r in enumerate(var_rows):
            alpha = r.get("alpha")
            alpha_str = _fmt_pct(alpha) if isinstance(alpha, (int, float)) and alpha < 1 else _esc(alpha)
            flag = r.get("flag") or ""
            floor = r.get("floor")
            if floor is not None:
                any_floor = True
            note = []
            if flag:
                note.append(flag)
            row = [alpha_str, _fmt_money(r.get("var")), _fmt_money(r.get("cvar")),
                   _fmt_money(floor) if floor is not None else "—", _esc(", ".join(note))]
            rows.append(row)
            cell_cls[(ri, 1)] = "bad"
            cell_cls[(ri, 2)] = "bad"
            if flag == "unbounded":
                cell_cls[(ri, 4)] = "warn"
        # wrap the table in a horizontal-scroll container so it never forces the
        # page sideways on a phone (#5); the floor note stays outside the scroller.
        var_table = ('<div class="tablewrap">'
                     + _table(["α", "VaR (loss)", "CVaR (loss)", "floor", "note"],
                              rows, cell_cls)
                     + "</div>")
        if any_floor:
            var_table += ('<p class="sub">VaR/CVaR reported as positive losses; '
                          "values clamped to the defined-risk floor where one exists.</p>")

    notes = prob.get("notes") or []
    notes_html = ("<ul>" + "".join(f"<li>{_esc(n)}</li>" for n in notes) + "</ul>") if notes else ""

    if not (cone or hist or var_table or notes_html):
        return ""
    out.append("<h2>Probability &amp; simulation</h2>")
    if cone or hist:
        out.append(f'<div class="grid2">{cone}{hist}</div>')
    if var_table:
        out.append('<div class="card">' + var_table + notes_html + "</div>")
    elif notes_html:
        out.append('<div class="card">' + notes_html + "</div>")
    return "".join(out)


# --- (6b) Combination / Decision -------------------------------------------
#
# Fires only when ctx["combination"] is truthy. Schema (all keys optional, every
# read goes through .get so a partial producer still renders a smaller block):
#
#   combination: {
#     "compare":   <combine.compare_subsets result: {"subsets":[...], "columns":[...]}>,
#     "candidates":[{"label","ids","scorecard":{...}, "liquidation":{"down":{...},
#                    "up":{...}}, "prob_liquidation", "recommendation",
#                    "dominating_risk", "tradeoff"}],
#     "charts":    {"combination_comparison": data-uri, "mc_fan_liquidation": data-uri},
#   }
#
# Inserted after _section_probability, before _section_risk_margin (contract §3c).

# Per-metric display formatter for the comparison table. Money-ish columns route
# through _fmt_money, pct columns through _fmt_pct, the rest through _fmt_num.
_COMBO_MONEY_COLS = frozenset({
    "net_cost_entry", "pnl_base", "maintenance", "excess_liquidity",
    "max_loss", "max_profit", "delta_equiv_shares",
})
_COMBO_PCT_COLS = frozenset({
    "liquidation_down_pct", "liquidation_up_pct",
})
_COMBO_GREEK_COLS = frozenset({"delta", "gamma", "theta_day", "vega", "rho"})
_COMBO_SIGNED_COLS = frozenset({"pnl_base", "excess_liquidity", "delta"})
# Friendlier column headers (fall back to the raw key prettified).
_COMBO_COL_LABELS = {
    "ids": "ids",
    "label": "subset",
    "net_cost_entry": "net cost",
    # entry_price=0 across the book ⇒ pnl_base ≈ gross mark value, NOT realized
    # P&L. It is the value at TODAY's spot (dt=0). Label it "Mark now*" so it is
    # unmistakably distinct from the per-card "EV* (RN,30d)" (the risk-neutral
    # EXPECTED value at the 30-day horizon) — different quantities, not a
    # contradiction. The * ties both back to _MARK_VALUE_NOTE.
    "pnl_base": "Mark now*",
    "delta": "Δ",
    "gamma": "Γ",
    "theta_day": "Θ/day",
    "vega": "ν",
    "rho": "ρ",
    "delta_equiv_shares": "Δ-equiv sh",
    "maintenance": "maint.",
    "excess_liquidity": "excess liq",
    # max_loss inherits the entry=0 distortion (collapses toward 0, no premium
    # paid) — star it so it ties back to _MARK_VALUE_NOTE like EV*/POP*.
    "max_loss": "max loss*",
    "max_profit": "max profit",
    "defined_risk": "defined?",
    "liquidation_down_pct": "liq ↓",
    "liquidation_up_pct": "liq ↑",
}


def _combo_col_label(col: str) -> str:
    return _COMBO_COL_LABELS.get(col, col.replace("_", " "))


def _combo_fmt_cell(col: str, value: Any) -> str:
    """Format one comparison-table cell for a given metric column."""
    if col in ("ids",):
        if isinstance(value, (list, tuple)):
            return _esc(",".join(str(v) for v in value))
        return _esc(value)
    if col in ("label",):
        return _esc(value)
    if col in ("defined_risk",):
        if value is None:
            return "—"
        return "yes" if value else "no"
    if col in _COMBO_PCT_COLS:
        if value is None:
            return "—"
        return _fmt_pct(value) if isinstance(value, (int, float)) else _esc(value)
    if col in _COMBO_MONEY_COLS:
        # max_loss/max_profit may be the string 'unbounded'
        if isinstance(value, str):
            return _esc(value)
        if col in ("delta_equiv_shares",):
            return _fmt_num(value, 1)
        return _fmt_money(value)
    if col in _COMBO_GREEK_COLS:
        return _fmt_num(value, 3 if col == "gamma" else 2)
    return _fmt_num(value, 2)


def _combination_compare_table(compare: Mapping[str, Any]) -> str:
    """Render the compare_subsets result as a desk comparison table."""
    subsets = compare.get("subsets") or []
    if not subsets:
        return ""
    columns = list(compare.get("columns") or [])
    if not columns:
        # derive a stable column order from the first row, label first
        first = subsets[0]
        columns = [k for k in first.keys() if k != "ids"]
        if "label" in columns:
            columns = ["label"] + [c for c in columns if c != "label"]
    headers = [_combo_col_label(c) for c in columns]
    rows: list[list[str]] = []
    cell_cls: dict[tuple[int, int], str] = {}
    for ri, sub in enumerate(subsets):
        row: list[str] = []
        for ci, col in enumerate(columns):
            val = sub.get(col)
            row.append(_combo_fmt_cell(col, val))
            if col in _COMBO_SIGNED_COLS and isinstance(val, (int, float)):
                cls = _signed_cls(val)
                if cls:
                    cell_cls[(ri, ci)] = cls
        rows.append(row)
    return _table(headers, rows, cell_cls)


def _combination_candidate_card(cand: Mapping[str, Any]) -> str:
    """One .card per ranked/decision candidate: KPIs + liquidation + verdicts."""
    label = _esc(cand.get("label") or "subset")
    ids = cand.get("ids")
    ids_txt = ""
    if isinstance(ids, (list, tuple)) and ids:
        ids_txt = f'<span class="pill">ids {_esc(",".join(str(i) for i in ids))}</span>'
    rank = cand.get("rank")
    rank_txt = f'<span class="pill">#{_esc(rank)}</span>' if rank is not None else ""
    out = [f'<div class="card"><h3>{label}{rank_txt}{ids_txt}</h3>']

    sc = cand.get("scorecard") or {}
    kpis: list[str] = []
    kpi_specs = [
        # EV / POP here are computed against mark value (entry=0) — gross, not
        # realized P&L. The * ties back to _MARK_VALUE_NOTE. EV* is the risk-neutral
        # EXPECTED value at the 30-day horizon — labelled "EV* (RN,30d)" so it is
        # unmistakably distinct from the comparison table's "Mark now*" (value at
        # today's spot, dt=0); they are different quantities, not a contradiction.
        ("EV* (RN,30d)", sc.get("ev_rn"), _fmt_money, True),
        ("POP* (mark, entry=0)", sc.get("pop"), _fmt_pct, False),
        ("P(max profit)", sc.get("p_max_profit"), _fmt_pct, False),
        ("P(max loss)*", sc.get("p_max_loss"), _fmt_pct, False),
        # VaR / CVaR are read off the mark-value P&L distribution (entry=0), so the
        # loss tail collapses toward 0 just like max_loss. Asterisk them so they
        # tie back to _MARK_VALUE_NOTE and are never read as real dollars-at-risk.
        ("VaR 95*", sc.get("var_95"), _fmt_money, False),
        ("CVaR 95*", sc.get("cvar_95"), _fmt_money, False),
        ("BP consumed", sc.get("bp_consumed"), _fmt_money, False),
        # Cap. efficiency = EV(rn) / BP — its numerator is the same gross mark-value
        # EV that carries the entry=0 caveat, so it inherits the distortion. And
        # Reward:risk = max_profit / max_loss collapses to ∞ when max_loss vanishes
        # to 0 at entry=0 (no premium paid). Asterisk both so they tie back to the
        # _MARK_VALUE_NOTE just like EV*/POP* — the cross-subset ranking is not
        # apples-to-apples until real fills are loaded.
        ("Cap. efficiency*", sc.get("capital_efficiency"), lambda v: _fmt_num(v, 3), True),
        ("Reward:risk*", sc.get("reward_risk"), lambda v: _fmt_num(v, 2), True),
    ]
    for lbl, val, fmt, signed in kpi_specs:
        if val is not None:
            cls = _signed_cls(val) if signed else ""
            kpis.append(_kpi(lbl, fmt(val), cls))
    if kpis:
        out.append(f'<div class="kpis">{"".join(kpis)}</div>')

    # Liquidation point + probability
    liq = cand.get("liquidation") or {}
    liq_rows: list[list[str]] = []
    for side, lbl in (("down", "Down liquidation"), ("up", "Up liquidation")):
        node = liq.get(side)
        if isinstance(node, Mapping) and node.get("spot") is not None:
            spot_v = node.get("spot")
            pct_v = node.get("pct")
            liq_rows.append([
                _esc(lbl),
                _fmt_num(spot_v, 2),
                _fmt_pct(pct_v) if pct_v is not None else "—",
            ])
    plq = cand.get("prob_liquidation")
    if plq is None:
        plq = sc.get("liquidation_prob")
    if liq_rows:
        out.append('<div class="card"><strong>Liquidation point (EL → 0)</strong>'
                   + _table(["side", "spot", "Δ% from spot"], liq_rows) + "</div>")
    if plq is not None:
        out.append(f'<p class="sub">P(liquidation before horizon): '
                   f'<strong class="warn">{_fmt_pct(plq)}</strong></p>')

    # Deterministic verdict sentences
    rec = cand.get("recommendation")
    if rec:
        out.append(f'<div class="card disc"><strong>Recommendation:</strong> {_esc(rec)}</div>')
    dom = cand.get("dominating_risk")
    if dom:
        out.append(f'<p class="sub"><strong class="bad">Dominating risk:</strong> {_esc(dom)}</p>')
    tradeoff = cand.get("tradeoff")
    if tradeoff:
        out.append(f'<p class="sub"><strong>Tradeoff:</strong> {_esc(tradeoff)}</p>')

    out.append("</div>")
    return "".join(out)


def _section_combination(ctx: Mapping[str, Any]) -> str:
    combo = ctx.get("combination") or {}
    if not combo:
        return ""
    out: list[str] = []

    compare = combo.get("compare") or {}
    table_html = _combination_compare_table(compare) if compare else ""

    charts = combo.get("charts") or {}
    cmp_chart = _chart_block(charts.get("combination_comparison"),
                             "Subset comparison")
    fan_chart = _chart_block(charts.get("mc_fan_liquidation"),
                             "MC path fan + liquidation barrier")

    candidates = combo.get("candidates") or []
    cand_html = "".join(_combination_candidate_card(c) for c in candidates)

    if not (table_html or cmp_chart or fan_chart or cand_html):
        return ""

    out.append("<h2>Combination / Decision</h2>")
    out.append('<div class="card disc"><strong>Decision-support only.</strong> '
               "Subset metrics combine the selected positions; maintenance is the "
               "per-position portfolio-margin sum (book-consistent). Liquidation "
               "points solve EL → 0 by spot move; probabilities are seeded MC.</div>")
    out.append(f'<div class="card caveat"><strong>Mark value, not P&amp;L.</strong> '
               f'{_MARK_VALUE_NOTE}</div>')
    if table_html:
        # The subset-comparison table is wide (16+ numeric columns); wrap it in a
        # horizontal-scroll container so it scrolls INSIDE the card on a phone
        # instead of forcing the whole page to overflow sideways.
        out.append('<div class="card"><div class="tablewrap">' + table_html + "</div></div>")
    if cmp_chart or fan_chart:
        out.append(f'<div class="grid2">{cmp_chart}{fan_chart}</div>')
    if cand_html:
        out.append(cand_html)
    return "".join(out)


# --- (7) Risk & margin -----------------------------------------------------

def _margin_liquidity_panel(margin: Mapping[str, Any]) -> str:
    """Broker vs model Excess-Liquidity panel (issue #2).

    Shows the broker-reported ExcessLiquidity (AUTHORITATIVE for risk-of-call)
    next to the model stress-grid estimate, with the gap + ratio and a one-line
    caveat that the model number is an estimate (intrinsic-only stress, runs
    optimistic) — NOT the broker's figure. Renders only when a broker EL is
    present; otherwise returns "" so the section degrades gracefully.
    """
    broker_el = margin.get("broker_excess_liquidity")
    if broker_el is None:
        return ""
    model_el = margin.get("excess_liquidity")

    kpis = [_kpi("Broker excess liq (AUTHORITATIVE)", _fmt_money(broker_el), "acc")]
    if isinstance(model_el, (int, float)):
        kpis.append(_kpi("Model EL (stress-grid est.)", _fmt_money(model_el),
                         _signed_cls(model_el)))
    gap = margin.get("el_gap")
    if gap is None and isinstance(model_el, (int, float)):
        gap = float(model_el) - float(broker_el)
    if gap is not None:
        # model − broker; positive means the model is rosier than the broker.
        kpis.append(_kpi("Gap (model − broker)", _fmt_money(gap),
                         "bad" if gap > 0 else ""))
    ratio = margin.get("el_ratio")
    if ratio is None and isinstance(model_el, (int, float)) and broker_el not in (None, 0):
        ratio = float(model_el) / float(broker_el)
    if ratio is not None:
        kpis.append(_kpi("Ratio (model ÷ broker)", f"{ratio:,.2f}×",
                         "bad" if ratio > 1.0 else ""))

    caveat = (
        '<div class="card banner"><strong>Risk-of-call = the broker number.</strong> '
        "The <strong>broker-reported Excess Liquidity is authoritative</strong> for "
        "whether positions get liquidated. The model figure is a "
        "<strong>stress-grid estimate</strong> (currently intrinsic-only — it ignores "
        "option time value and vol, so it runs optimistic and overstates the cushion). "
        "It is shown for context only; <strong>do not treat the model EL as the "
        "broker's</strong>.</div>"
    )
    return ('<div class="card"><strong>Margin / Liquidity — broker vs model</strong>'
            f'<div class="kpis">{"".join(kpis)}</div></div>' + caveat)


def _section_risk_margin(ctx: Mapping[str, Any]) -> str:
    c = _charts(ctx)
    ladder = _chart_block(c.get("delta_ladder"), "Delta ladder")
    timeline = _chart_block(c.get("expiration_timeline"), "Expiration timeline")
    waterfall = _chart_block(c.get("margin_waterfall"), "Margin waterfall")  # v2; renders if present

    margin = ctx.get("margin") or {}
    liquidity_panel = _margin_liquidity_panel(margin)
    margin_card = ""
    if margin:
        rows = []
        for label, key, fmt in [
            ("Net Liq", "netliq", _fmt_money),
            ("Maintenance margin (model, stress-grid est.)", "maintenance", _fmt_money),
            ("Excess liquidity (model est.)", "excess_liquidity", _fmt_money),
        ]:
            if margin.get(key) is not None:
                rows.append([_esc(label), fmt(margin.get(key))])
        binding = margin.get("binding_node")
        if isinstance(binding, Mapping) and binding:
            desc = ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in binding.items())
            rows.append(["Binding stress node", desc])
        if rows:
            margin_card = ('<div class="card"><strong>Portfolio margin (model stress grid)</strong>'
                           + _table(["item", "value"], rows) + "</div>")

    if not (ladder or timeline or waterfall or margin_card or liquidity_panel):
        return ""
    out = ["<h2>Risk &amp; margin</h2>"]
    if liquidity_panel:
        out.append(liquidity_panel)
    if margin_card:
        out.append(margin_card)
    if ladder or timeline:
        out.append(f'<div class="grid2">{ladder}{timeline}</div>')
    if waterfall:
        out.append(waterfall)
    return "".join(out)


# --- (8) Strategy advice ---------------------------------------------------

def _section_strategy(ctx: Mapping[str, Any]) -> str:
    st = ctx.get("strategy") or {}
    if not st:
        return ""
    out = ["<h2>Strategy advice</h2>"]
    out.append('<div class="card banner"><strong>Not an order.</strong> '
               "Decision-support only — all numbers carry the stated assumptions "
               "(vol, measure, drift). Verify before acting.</div>")
    posture = st.get("posture")
    if posture:
        out.append(f'<div class="card"><strong>Posture:</strong> {_esc(posture)}</div>')
    advice = st.get("advice") or []
    if advice:
        items = "".join(f"<li>{_esc(a)}</li>" for a in advice)
        out.append(f'<div class="card"><ul>{items}</ul></div>')
    ivr = st.get("iv_rank") or {}
    if ivr:
        rows = [[_esc(k), _fmt_num(v, 2) if isinstance(v, (int, float)) else _esc(v)]
                for k, v in ivr.items()]
        out.append('<div class="card"><strong>IV rank / percentile gate</strong>'
                   + _table(["metric", "value"], rows) + "</div>")
    return "".join(out) if len(out) > 2 else ""


# --- (9) Catalysts ---------------------------------------------------------

def _section_catalysts(ctx: Mapping[str, Any]) -> str:
    cat = ctx.get("catalysts") or {}
    if not cat:
        return ""
    out = []
    earnings = cat.get("earnings") or []
    divs = cat.get("dividends") or []
    assign = cat.get("assignment") or cat.get("early_assignment") or []

    if earnings:
        rows = [[_as_iso(e.get("date")), _esc(e.get("label") or e.get("symbol") or ""),
                 _esc("confirmed" if e.get("confirmed") else "estimated")] for e in earnings]
        out.append("<h3>Earnings in window</h3>" + _table(["date", "event", "status"], rows))
    if divs:
        rows = [[_as_iso(d.get("date") or d.get("ex_date")), _esc(d.get("symbol") or ""),
                 _fmt_num(d.get("amount") or d.get("cash"), 2)] for d in divs]
        out.append("<h3>Ex-dividends in window</h3>" + _table(["ex-date", "symbol", "amount"], rows))
    if assign:
        rows = []
        for a in assign:
            if isinstance(a, Mapping):
                rows.append([_esc(a.get("leg") or a.get("symbol") or ""),
                             _esc(a.get("risk") or a.get("flag") or ""),
                             _esc(a.get("detail") or a.get("note") or "")])
            else:
                rows.append([_esc(a), "", ""])
        out.append("<h3>Early-assignment flags</h3>" + _table(["leg", "risk", "detail"], rows))

    if not out:
        return ""
    return "<h2>Catalysts</h2>" + '<div class="card">' + "".join(out) + "</div>"


# --- reconciliation badges -------------------------------------------------

def _section_reconcile(ctx: Mapping[str, Any]) -> str:
    rec = ctx.get("reconcile") or []
    if not rec:
        return ""
    badges = []
    for r in rec:
        passed = bool(r.get("passed"))
        name = _esc(r.get("name") or "check")
        gap = r.get("gap_in_se")
        detail = f" ({_fmt_num(gap, 2)}σ)" if gap is not None else ""
        klass = "ok" if passed else "fail"
        mark = "✓" if passed else "✗"
        badges.append(f'<span class="badge {klass}">{mark} {name}{detail}</span>')
    return ('<div class="card"><strong>MC ↔ analytic reconciliation</strong><br>'
            + "".join(badges) + "</div>")


# --- (10) Appendix ---------------------------------------------------------

def _section_appendix(ctx: Mapping[str, Any]) -> str:
    c = _charts(ctx)
    # appendix shows any charts not already surfaced + a full assumptions dump
    rendered_ids = {
        "greeks_vs_spot", "scenario_tornado", "pnl_surface_spot_vol", "pnl_surface_spot_time",
        "probability_cone", "mc_histogram", "delta_ladder", "expiration_timeline",
        "margin_waterfall",
        # combination charts are surfaced in _section_combination
        "combination_comparison", "mc_fan_liquidation",
    }
    extra = []
    for cid, val in c.items():
        if cid in rendered_ids:
            continue
        # Per-position payoff thumbnails are already shown on their position card
        # (card["thumb"]); only let the per-position greeks small-multiples through
        # to the appendix so we don't double-render the payoff.
        if cid.startswith("pos") and cid.endswith("_payoff"):
            continue
        blk = _chart_block(val, cid.replace("_", " "))
        if blk:
            extra.append(blk)
    if not extra:
        return ""
    return "<h2>Appendix — additional charts</h2>" + "".join(extra)


def _section_footer(ctx: Mapping[str, Any]) -> str:
    meta = ctx.get("meta", {})
    mode = _esc(meta.get("mode") or "book")
    return (
        '<p class="foot">Generated by <code>bursahack.options</code> — '
        "self-contained tearsheet (charts embedded as base64; zero CDN, zero JS). "
        f"Mode: <code>{mode}</code>. Reproducible via "
        "<code>scripts/run_options_analysis.py</code>. "
        "Research artifact / decision-support only — not investment advice.</p>"
    )


# ============================================================================
# Body assembly + page templating
# ============================================================================

_SECTIONS = (
    _section_header,
    _section_warnings,
    _section_summary,
    _section_positions,
    _section_greeks_dash,
    _section_scenario,
    _section_probability,
    _section_combination,
    _section_risk_margin,
    _section_strategy,
    _section_catalysts,
    _section_reconcile,
    _section_appendix,
    _section_footer,
)


def _build_body(ctx: Mapping[str, Any]) -> str:
    return "\n".join(frag for frag in (sec(ctx) for sec in _SECTIONS) if frag)


# Page shell as a string.Template -- ``$body`` / ``$style`` / ``$title`` only.
_PAGE_TEMPLATE = Template(
    "<!DOCTYPE html>\n"
    '<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>$title</title>\n"
    "<style>$style</style>\n"
    "</head>\n<body>\n"
    '<div class="wrap">\n$body\n</div>\n'
    "</body>\n</html>\n"
)

# jinja2 mirror of the shell (used when jinja2 is importable). Body is pre-built
# HTML, so it is marked safe; jinja2 only assembles the outer shell.
_JINJA_PAGE = (
    "<!DOCTYPE html>\n"
    '<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>{{ title }}</title>\n"
    "<style>{{ style }}</style>\n"
    "</head>\n<body>\n"
    '<div class="wrap">\n{{ body | safe }}\n</div>\n'
    "</body>\n</html>\n"
)


def _render_page(title: str, body: str) -> str:
    """Render the outer page shell. jinja2 primary, string.Template fallback."""
    try:
        import jinja2  # lazy, optional

        tmpl = jinja2.Template(_JINJA_PAGE, autoescape=False)
        return tmpl.render(title=_esc(title), style=_CSS, body=body)
    except Exception:
        # bare-interpreter fallback -- identical output shape, no jinja2 needed
        return _PAGE_TEMPLATE.substitute(title=_esc(title), style=_CSS, body=body)


# ============================================================================
# Public renderer (frozen contract signature)
# ============================================================================

def build_html_report(ctx: Mapping[str, Any] | ReportContext,
                      out_path: str | Path | None = None,
                      mode: str = "book") -> str:
    """Render ``ctx`` into one self-contained HTML tearsheet and write it.

    Contract-frozen entry point. ``ctx`` is a nested dict (or ``ReportContext``)
    -- see the module docstring for the schema. Every chart is embedded as a
    base64 ``data:`` URI; the output references zero external resources.

    jinja2 is used for the outer shell when importable; otherwise a
    ``string.Template`` fallback produces the same page, so the report builds on
    a bare interpreter (contract requirement).

    Args:
        ctx: analysis context (numbers, tables, base64 charts).
        out_path: destination HTML path. If None, defaults to
            ``web/public/<underlying_or_book>_options_report.html``.
        mode: report mode tag recorded in the footer (``book`` / ``single`` / …).

    Returns:
        The absolute path of the written HTML file (as a string).
    """
    if isinstance(ctx, ReportContext):
        ctx = ctx.as_dict()
    ctx = dict(ctx)  # shallow copy; never mutate the caller's dict
    meta = dict(ctx.get("meta") or {})
    meta.setdefault("mode", mode)
    ctx["meta"] = meta

    title = meta.get("title") or "Options Book — Desk Tearsheet"
    body = _build_body(ctx)
    page = _render_page(title, body)

    out_path = _resolve_out_path(out_path, meta)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return str(out_path)


def _resolve_out_path(out_path: str | Path | None, meta: Mapping[str, Any]) -> Path:
    if out_path is not None:
        return Path(out_path)
    unders = meta.get("underlyings") or []
    stem = "book"
    if len(unders) == 1:
        stem = str(unders[0]).lower()
    elif unders:
        stem = "_".join(str(u).lower() for u in unders[:3])
    return WEB_PUBLIC / f"{stem}_options_report.html"


# ============================================================================
# Orchestrator: Book + Account -> ctx -> HTML
# ============================================================================

def build_ctx(book: Any, account: Any | None = None, *,
              market_by_name: Mapping[str, Any] | None = None,
              measure: Any = None, mu: float | None = None,
              seed: int = 7, mc_paths: int = 1_000_000,
              charts: bool = True) -> dict[str, Any]:
    """Run the analytics + chart pipeline over a ``Book`` and assemble ``ctx``.

    Every sibling module is imported lazily and each analytics call is wrapped so
    that a not-yet-built or failing module degrades to an omitted section rather
    than aborting the whole report. This keeps ``report.py`` composable while the
    rest of ``bursahack.options`` is built in parallel.

    Args:
        book: a ``bursahack.options.types.Book``.
        account: optional ``Account`` (supplies NLV — the single source, P4).
        market_by_name: ``{underlying: MarketState}``; if omitted, inferred from
            the book's positions where possible.
        measure / mu: probability measure + real-world drift for prob/MC sections.
        seed / mc_paths: MC determinism knobs (recorded in assumptions).
        charts: when True, render charts to ``results/options/`` and embed them.

    Returns:
        A ``ctx`` dict ready for ``build_html_report``.
    """
    ctx = ReportContext()

    # ---- meta / assumptions -------------------------------------------------
    market_by_name = dict(market_by_name or {})
    underlyings: list[str] = []
    spot: dict[str, float] = {}
    r_val: float | None = None
    q_val: float | None = None
    vol_src = "MarketState.sigma (flat)"
    asof = getattr(book, "asof", None)

    positions = list(getattr(book, "positions", []) or [])
    for pos in positions:
        sym = getattr(pos, "underlying", "") or ""
        if sym and sym not in underlyings:
            underlyings.append(sym)
        ms = market_by_name.get(sym)
        if ms is not None:
            spot[sym] = getattr(ms, "spot", None)
            if r_val is None:
                r_val = getattr(ms, "r", None)
                q_val = getattr(ms, "q", None)

    nlv = getattr(account, "netliq", None) if account is not None else None

    measure_str = None
    if measure is not None:
        measure_str = getattr(measure, "value", None) or str(measure)

    ctx.meta = {
        "title": _book_title(underlyings),
        "generated": datetime.now().replace(microsecond=0),
        "asof": asof,
        "underlyings": underlyings,
        "spot": spot,
        "nlv": nlv,
        "assumptions": {
            "r": r_val if r_val is not None else 0.045,
            "q": q_val if q_val is not None else 0.0,
            "vol_source": vol_src,
            "model": "Black-Scholes / CRR + GBM Monte-Carlo",
            "measure": measure_str or "rn",
            "mu": mu,
            "seed": seed,
            "mc_paths": mc_paths,
        },
    }

    # ---- per-position cards (classify + economics) -------------------------
    ctx.positions = _safe(lambda: _build_position_cards(positions, market_by_name), default=[]) or []

    # ---- book summary (net greeks + posture) ------------------------------
    ctx.summary = _safe(lambda: _build_summary(book, market_by_name, account), default={}) or {}

    # ---- charts (lazy viz) -------------------------------------------------
    if charts:
        ctx.charts = _safe(lambda: _render_charts(positions, market_by_name, ctx), default={}) or {}

    return ctx.as_dict()


def build_report(book: Any, account: Any | None = None,
                 out_path: str | Path | None = None, *,
                 market_by_name: Mapping[str, Any] | None = None,
                 measure: Any = None, mu: float | None = None,
                 seed: int = 7, mc_paths: int = 1_000_000,
                 mode: str = "book", charts: bool = True) -> str:
    """Orchestrate analytics + charts into a single self-contained HTML tearsheet.

    The assignment's top-level entry point: ``build_report(book, account, out_path)``.
    Builds the ``ctx`` (``build_ctx``) then renders it (``build_html_report``).

    Returns the absolute path of the written HTML file.
    """
    ctx = build_ctx(
        book, account,
        market_by_name=market_by_name,
        measure=measure, mu=mu, seed=seed, mc_paths=mc_paths, charts=charts,
    )
    return build_html_report(ctx, out_path, mode=mode)


# ---- orchestration helpers (all lazy + defensive) -------------------------

def _safe(fn, default=None):
    """Run a pipeline step; swallow not-yet-built / runtime failures.

    Sibling analytics/viz modules may not exist yet (parallel build) or may raise
    on partial data. A failed step yields ``default`` so the report still builds.
    """
    try:
        return fn()
    except Exception:
        return default


def _book_title(underlyings: Sequence[str]) -> str:
    if not underlyings:
        return "Options Book — Desk Tearsheet"
    if len(underlyings) == 1:
        return f"{underlyings[0]} Options — Desk Tearsheet"
    return f"Options Book ({', '.join(underlyings[:3])}{'…' if len(underlyings) > 3 else ''}) — Desk Tearsheet"


def _build_position_cards(positions: Sequence[Any],
                          market_by_name: Mapping[str, Any]) -> list[dict[str, Any]]:
    from bursahack.options import structure as _structure  # lazy

    cards: list[dict[str, Any]] = []
    for pos in positions:
        sym = getattr(pos, "underlying", "")
        legs = list(getattr(pos, "legs", []) or [])
        card: dict[str, Any] = {
            "name": getattr(pos, "name", "") or sym or "Position",
            "underlying": sym,
            "legs": [_leg_to_dict(leg) for leg in legs],
        }
        ms = market_by_name.get(sym)

        strat = _safe(lambda: _structure.classify(pos))
        if strat is not None:
            card["classification"] = getattr(strat, "name", None)

        # economics: net premium, breakevens, max P/L, R:R, greeks
        if ms is not None:
            s_grid = _safe(lambda: _spot_grid(ms))
            econ = _safe(lambda: _structure.economics(pos, ms, s_grid or []))
            if econ is not None:
                card["entry"] = getattr(econ, "net_premium", None)
                card["max_profit"] = getattr(econ, "max_profit", None)
                card["max_loss"] = getattr(econ, "max_loss", None)
                # Surface unbounded tails so the card can say "∞" instead of
                # silently dropping a genuinely open up/down side.
                card["unbounded_side"] = getattr(econ, "unbounded_side", None)
                # A long-call up-tail is DEFINED-risk (side stays None) but has
                # unbounded PROFIT — defined_risk returns max_profit='unbounded'
                # which economics() maps to None; flag it for the card so it shows
                # "∞" rather than dropping the row. Guard on a real max_loss so we
                # only do this when economics actually succeeded (not on errors).
                if (card["max_profit"] is None
                        and card["max_loss"] is not None
                        and getattr(econ, "defined_risk", True)):
                    card["max_profit_unbounded"] = True
                be = getattr(econ, "breakevens", None)
                if be is not None:
                    card["breakevens"] = list(be)
                rr = getattr(econ, "rr", None)
                if rr is not None:
                    card["rr"] = rr
                g = getattr(econ, "net_greeks", None)
                if g is not None:
                    card["greeks"] = _greeks_to_dict(g)
        cards.append(card)
    return cards


def _build_summary(book: Any, market_by_name: Mapping[str, Any],
                   account: Any | None) -> dict[str, Any]:
    from bursahack.options import book as _book  # lazy

    summary: dict[str, Any] = {}
    gr = _safe(lambda: _book.net_greeks(book, market_by_name))
    if gr is not None:
        totals = getattr(gr, "totals", None)
        if totals:
            summary["dollar_greeks"] = dict(totals)

    netliq = getattr(account, "netliq", None) if account is not None else None
    if netliq is not None:
        posture = _safe(lambda: _book.income_posture(
            book, market_by_name, betas={}, S_spy=0.0, netliq=netliq))
        if posture is not None:
            summary["income_posture"] = dict(posture) if isinstance(posture, Mapping) else posture
    return summary


def _render_charts(positions: Sequence[Any], market_by_name: Mapping[str, Any],
                   ctx: ReportContext) -> dict[str, str]:
    from bursahack.options.viz import charts as _charts_mod  # lazy

    out_dir = OPTIONS_RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_ctx = {
        "positions": positions,
        "market_by_name": market_by_name,
        "meta": ctx.meta,
    }
    rendered = _safe(lambda: _charts_mod.render_all_charts(chart_ctx, out_dir), default={})
    uris: dict[str, str] = {}
    if isinstance(rendered, Mapping):
        for cid, val in rendered.items():
            uri = _coerce_chart(val)
            if uri:
                uris[cid] = uri
    return uris


def _spot_grid(ms: Any, n: int = 41, span: float = 0.4) -> list[float]:
    s = float(getattr(ms, "spot", 0.0) or 0.0)
    if s <= 0:
        return []
    lo, hi = s * (1 - span), s * (1 + span)
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def _leg_to_dict(leg: Any) -> dict[str, Any]:
    right = getattr(leg, "right", None)
    right_str = getattr(right, "value", None) or str(right or "")
    return {
        "right": right_str,
        "strike": getattr(leg, "strike", None),
        "expiry": getattr(leg, "expiry", None),
        "qty": getattr(leg, "qty", None),
        "mult": getattr(leg, "mult", None),
        "entry_price": getattr(leg, "entry_price", None),
    }


def _greeks_to_dict(g: Any) -> dict[str, Any]:
    if isinstance(g, Mapping):
        return dict(g)
    keys = ("delta", "gamma", "theta_day", "theta_yr", "vega", "rho",
            "vanna", "vomma", "charm_day", "speed", "zomma", "color_day")
    return {k: getattr(g, k, None) for k in keys if getattr(g, k, None) is not None}


# ============================================================================
# Optional PDF mirror (reportlab; lazy). Full styled PDF is [DEFERRED-v2];
# this is a lightweight text+chart fallback so a PDF can still be produced.
# ============================================================================

def build_pdf_report(ctx: Mapping[str, Any] | ReportContext,
                     out_path: str | Path) -> str:
    """Optional reportlab PDF mirror of the tearsheet (lightweight).

    The fully-styled PDF is deferred to v2; this renders the headline KPIs,
    book-summary greeks, position list and any base64 charts into a portable PDF
    via reportlab (a core dep). reportlab is imported lazily; raises ImportError
    with a clear message if unavailable.

    Returns the written PDF path.
    """
    if isinstance(ctx, ReportContext):
        ctx = ctx.as_dict()
    try:
        from reportlab.lib.pagesizes import A4  # lazy
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as _canvas
    except ImportError as exc:  # pragma: no cover - dep guaranteed in core
        raise ImportError(
            "build_pdf_report requires reportlab (a core BursaHack dependency)."
        ) from exc

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = ctx.get("meta", {})
    page_w, page_h = A4
    c = _canvas.Canvas(str(out_path), pagesize=A4)
    y = page_h - 20 * mm

    def _line(text: str, size: int = 11, dy: float = 6 * mm, bold: bool = False) -> None:
        nonlocal y
        if y < 25 * mm:
            c.showPage()
            y = page_h - 20 * mm
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(20 * mm, y, text[:110])
        y -= dy

    _line(str(meta.get("title") or "Options Book — Desk Tearsheet"), 16, 9 * mm, bold=True)
    _line(f"as-of {_as_iso(meta.get('asof') or '')} · generated {_as_iso(meta.get('generated') or datetime.now())}", 9)

    a = meta.get("assumptions") or {}
    if a:
        _line("Assumptions", 12, 7 * mm, bold=True)
        for k, v in a.items():
            if v is not None:
                _line(f"  {k}: {v}", 9, 5 * mm)

    summary = ctx.get("summary") or {}
    dg = summary.get("dollar_greeks") or {}
    if dg:
        _line("Net dollar-greeks", 12, 7 * mm, bold=True)
        for k, v in dg.items():
            _line(f"  {k}: {v}", 9, 5 * mm)

    positions = ctx.get("positions") or []
    if positions:
        _line("Positions", 12, 7 * mm, bold=True)
        for p in positions:
            _line(f"  {p.get('name','')} [{p.get('classification','')}]", 10, 5 * mm, bold=True)
            for leg in p.get("legs", []) or []:
                _line(f"     {_leg_label(leg)}", 8, 4.5 * mm)

    # embed charts (decode base64 data-URIs)
    for cid, val in (ctx.get("charts") or {}).items():
        uri = _coerce_chart(val)
        if not uri:
            continue
        try:
            raw = base64.b64decode(uri.split(",", 1)[1])
            img = ImageReader(io.BytesIO(raw))
            iw, ih = img.getSize()
            max_w = page_w - 40 * mm
            scale = min(1.0, max_w / iw)
            draw_h = ih * scale
            if y - draw_h < 25 * mm:
                c.showPage()
                y = page_h - 20 * mm
            c.drawImage(img, 20 * mm, y - draw_h, width=iw * scale, height=draw_h,
                        preserveAspectRatio=True, mask="auto")
            y -= draw_h + 6 * mm
        except Exception:
            continue

    c.showPage()
    c.save()
    return str(out_path)


__all__ = [
    "ReportContext",
    "build_html_report",
    "build_report",
    "build_ctx",
    "build_pdf_report",
    "fig_to_base64",
    "png_path_to_base64",
]
