"""Self-contained-report contract tests for the options decision/report layer.

Two surfaces are exercised:

  1. The CLI wiring (``scripts/run_options_analysis.py``) end-to-end on the real
     9-position TSLA book: load -> markets -> per-position cards -> per-position
     payoff/greeks thumbnails -> report ctx -> ``report.build_html_report``. We
     assert the emitted HTML is fully self-contained (every image is a base64
     ``data:`` URI, zero ``http(s)://`` references, zero ``<script>``) and that it
     carries more than one position card (the per-position thumbnail wiring runs).

  2. The Combination / Decision section (``report._section_combination``) fed a
     stub ``combination`` ctx in the documented schema, asserting the comparison
     table + per-candidate decision card (scorecard KPIs, liquidation point/%,
     P(liquidation), recommendation/dominating-risk/tradeoff) all render and the
     output stays self-contained.

The ``combine`` / ``decision`` analytics modules are built by sibling agents and
may not have landed yet; this test does NOT depend on them existing — surface (1)
runs the CLI with no ``--subsets`` (combination stage skips cleanly) and surface
(2) feeds a hand-built combination ctx straight to the renderer. Both must hold
regardless of the decision-layer build state.

References:
  * BUILD CONTRACT — Decision/Combination layer, FILE 3 (report) + FILE 5 (tests)
  * src/bursahack/options/report.py  (build_html_report, _section_combination)
  * scripts/run_options_analysis.py  (_report_ctx, _render, _combination)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from bursahack.options import bookio, report
from bursahack.paths import REPO_ROOT

# Matplotlib is required to render the embedded charts; skip the whole module
# (rather than fail) on a headless interpreter without it.
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")  # non-interactive backend for deterministic, file-free render

TSLA_BOOK = REPO_ROOT / "configs" / "options" / "tsla_book_2026-06-18.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_cli_module():
    """Import scripts/run_options_analysis.py as a module (it is a script, not a
    package member). Returns the loaded module object."""
    cli_path = REPO_ROOT / "scripts" / "run_options_analysis.py"
    spec = importlib.util.spec_from_file_location("_run_options_analysis", cli_path)
    assert spec and spec.loader, "could not build import spec for the CLI"
    mod = importlib.util.module_from_spec(spec)
    # Register so dataclasses / typing inside the module resolve cleanly.
    sys.modules.setdefault("_run_options_analysis", mod)
    spec.loader.exec_module(mod)
    return mod


class _Args:
    """Minimal argparse.Namespace stand-in for the CLI builders."""

    def __init__(self, **kw):
        self.demo = False
        self.offline = True
        self.fetch = None
        self.measure = "rn"
        self.mu = None
        self.seed = 7
        self.mc_paths = 200_000
        self.charts_only = False
        self.report_out = None
        self.no_report = False
        self.quiet = True
        self.subsets = None
        self.decide = False
        self.horizon_days = 30
        self._book_name = "tsla_book_2026-06-18"
        for k, v in kw.items():
            setattr(self, k, v)


def _data_uri():
    """A real (tiny) base64 PNG data-URI via the report's own fig encoder."""
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(2, 1))
    plt.plot([0, 1, 2], [0, 1, 0])
    return report.fig_to_base64(fig)


def _assert_self_contained(html: str) -> None:
    """The locked self-contained guarantees (contract FILE 5 / report docstring)."""
    assert "data:image" in html, "report has no embedded base64 images"
    assert "http://" not in html, "report references an http:// resource"
    assert "https://" not in html, "report references an https:// resource"
    assert "<script" not in html, "report embeds a <script> tag (not self-contained)"


# ---------------------------------------------------------------------------
# 1 — CLI end-to-end on the real TSLA book
# ---------------------------------------------------------------------------
def test_cli_report_self_contained_with_position_cards(tmp_path):
    """Drive the CLI builders over the 9-position TSLA book and assert the HTML is
    self-contained and carries per-position cards (with embedded thumbnails)."""
    cli = _load_cli_module()
    book = bookio.load_book(TSLA_BOOK)
    assert len(book.positions) == 9

    args = _Args(report_out=str(tmp_path / "tsla_report.html"))

    stages = {name: cli._Stage(name) for name in (
        "load", "market", "economics", "book", "margin",
        "combination", "decision", "reconcile", "charts", "report",
    )}

    markets = cli._resolve_markets(book, args, stages, quiet=True)
    assert markets.get("TSLA") is not None

    cards = cli._position_cards(book, markets, args, stages, quiet=True)
    aggregates = cli._book_aggregates(book, markets, args, stages, quiet=True)
    combination = cli._combination(book, markets, args, stages, quiet=True)

    render_out = cli._render(book, markets, cards, aggregates, combination,
                             args, stages, quiet=True)

    report_path = render_out.get("report_path")
    assert report_path, "CLI did not write a report"
    html = Path(report_path).read_text(encoding="utf-8")

    _assert_self_contained(html)
    # per-position cards render (more than one -> the per-position loop ran)
    assert html.count('class="pos"') > 1, "fewer than 2 position cards rendered"
    # the per-position payoff thumbnails embed as base64 (well above the 3
    # book-level charts -> the thumbnail wiring fired)
    assert html.count("data:image") > 3


def test_cli_runs_clean_exit_zero(tmp_path):
    """The CLI's run() returns a clean (0) exit on the demo book with no --subsets
    (the combination/decision stages skip and never flip the gate)."""
    cli = _load_cli_module()
    code = cli.run([
        str(TSLA_BOOK),
        "--report-out", str(tmp_path / "r.html"),
        "--quiet",
    ])
    assert code == 0


# ---------------------------------------------------------------------------
# 2 — Combination / Decision section
# ---------------------------------------------------------------------------
def _stub_combination_ctx(uri: str) -> dict:
    return {
        "meta": {
            "title": "TSLA Options — Desk Tearsheet",
            "underlyings": ["TSLA"],
            "spot": {"TSLA": 389.80},
            "assumptions": {"r": 0.045, "q": 0.0},
        },
        "positions": [
            {"name": "P1", "underlying": "TSLA", "legs": [], "thumb": uri},
            {"name": "P2", "underlying": "TSLA", "legs": [], "thumb": uri},
        ],
        "charts": {
            "combination_comparison": uri,
            "mc_fan_liquidation": uri,
        },
        "combination": {
            "compare": {
                "subsets": [
                    {"ids": [1, 2], "label": "1,2", "net_cost_entry": 12000.0,
                     "pnl_base": 1234.5, "delta": 50.0, "gamma": 0.1,
                     "theta_day": -3.0, "vega": 40.0, "rho": 10.0,
                     "delta_equiv_shares": 250.0, "maintenance": 9999.0,
                     "excess_liquidity": -100.0, "max_loss": "unbounded",
                     "max_profit": 50000.0, "defined_risk": False,
                     "liquidation_down_pct": -0.22, "liquidation_up_pct": None},
                    {"ids": [8, 9], "label": "8,9", "net_cost_entry": -3000.0,
                     "pnl_base": -500.0, "delta": -120.0, "gamma": -0.05,
                     "theta_day": 8.0, "vega": -60.0, "rho": -5.0,
                     "delta_equiv_shares": -300.0, "maintenance": 45000.0,
                     "excess_liquidity": 80000.0, "max_loss": "unbounded",
                     "max_profit": 3000.0, "defined_risk": False,
                     "liquidation_down_pct": None, "liquidation_up_pct": -0.18},
                ],
                "columns": ["label", "pnl_base", "delta", "delta_equiv_shares",
                            "maintenance", "excess_liquidity", "max_loss",
                            "liquidation_down_pct", "defined_risk"],
            },
            "candidates": [
                {"rank": 1, "label": "1,5,6,8", "ids": [1, 5, 6, 8],
                 "scorecard": {"ev_rn": 500.0, "pop": 0.62, "p_max_profit": 0.12,
                               "p_max_loss": 0.05, "var_95": 3000.0,
                               "cvar_95": 4000.0, "bp_consumed": 12000.0,
                               "capital_efficiency": 0.0417, "reward_risk": 1.8},
                 "liquidation": {"down": {"spot": 310.0, "pct": -0.20},
                                 "up": {"spot": None}},
                 "prob_liquidation": 0.13,
                 "recommendation": "Strong expected value with adequate cushion.",
                 "dominating_risk": "down-side liquidation at -20% spot",
                 "tradeoff": "highest EV but thinnest EL cushion"},
            ],
            "charts": {
                "combination_comparison": uri,
                "mc_fan_liquidation": uri,
            },
        },
    }


def test_combination_section_renders(tmp_path):
    """A stub combination ctx renders the Combination / Decision section: the
    comparison table, the per-candidate decision card (scorecard KPIs +
    liquidation point + P(liquidation) + verdict sentences), and stays
    self-contained."""
    uri = _data_uri()
    ctx = _stub_combination_ctx(uri)
    out = report.build_html_report(ctx, out_path=str(tmp_path / "combo.html"),
                                   mode="book")
    html = Path(out).read_text(encoding="utf-8")

    _assert_self_contained(html)
    assert "Combination / Decision" in html
    # comparison table content
    assert "1,2" in html and "8,9" in html
    assert "unbounded" in html  # max_loss string passthrough
    # candidate decision card
    assert "#1" in html
    assert "Strong expected value" in html
    assert "down-side liquidation" in html
    assert "thinnest EL cushion" in html
    assert "P(liquidation" in html


def test_combination_section_empty_is_omitted(tmp_path):
    """An empty/absent combination ctx omits the section (no header)."""
    uri = _data_uri()
    ctx = {
        "meta": {"title": "T", "underlyings": ["TSLA"],
                 "spot": {"TSLA": 389.8}, "assumptions": {}},
        "positions": [{"name": "P1", "legs": [], "thumb": uri}],
        "charts": {},
        "combination": {},
    }
    out = report.build_html_report(ctx, out_path=str(tmp_path / "empty.html"),
                                   mode="book")
    html = Path(out).read_text(encoding="utf-8")
    assert "Combination / Decision" not in html
    # but the rest of the report still builds + stays self-contained
    _assert_self_contained(html)
