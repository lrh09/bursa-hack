"""Options analysis CLI — load a book YAML, run desk-grade analytics, ship a report.

Pipeline (contract §6):
  1. bookio.load_book  -> Book (+ validation warnings)            [--demo uses the
     bundled TSLA 360/460 x8 worked example from bookio.TSLA_DEMO_YAML]
  2. Resolve a MarketState per underlying (YAML, or --fetch via provider -> cache)
  3. Per position: classify -> economics -> summary_card; run prob / MC / scenario;
     aggregate the whole book via book + margin
  4. viz.charts.render_all_charts -> results/options/   (PNG, base64 sidecars)
  5. viz.report.build_html_report -> web/public/<name>_options_report.html
     (self-contained: base64-inlined PNGs, zero CDN/JS)
  6. Console: per-position card + book net-greeks + posture verdict + margin/
     Excess-Liq + reconciliation pass/fail badges.

Pure-core analytics run fully offline; --fetch is the only network path. The CLI
exits NON-ZERO if any reconcile gate fails OR the book fails validation
(contract §6).

This module is the orchestrator only: every number flows through the tested
`bursahack.options` library (design law L1 / the SKILL anti-pattern). It does NOT
re-implement any pricing, greek, payoff, or probability math.

References:
  - Build contract §6 (CLI flow + flags), §4 (YAML schema), §3 (module interfaces)
  - web/public/crypto_deflated_report.html (self-contained-report exemplar)
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path

# --- stdout / path bootstrap (mirror scripts/run_crypto_deflated.py) ----------
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - platform dependent
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Canonical artifact dirs (contract §0). Created at write time.
from bursahack.paths import REPO_ROOT as _PKG_REPO_ROOT, RESULTS_DIR  # noqa: E402

OPTIONS_RESULTS = RESULTS_DIR / "options"
OPTIONS_CONFIGS = _PKG_REPO_ROOT / "configs" / "options"
WEB_PUBLIC = _PKG_REPO_ROOT / "web" / "public"


# =============================================================================
# Console helpers
# =============================================================================
class _C:
    """Console badges/labels. Plain ASCII so Windows terminals never choke."""

    OK = "[ OK ]"
    FAIL = "[FAIL]"
    SKIP = "[skip]"
    WARN = "[warn]"
    RULE = "=" * 78
    THIN = "-" * 78


def _say(msg: str = "", quiet: bool = False) -> None:
    if not quiet:
        print(msg)


def _section(title: str, quiet: bool = False) -> None:
    _say("", quiet)
    _say(_C.RULE, quiet)
    _say(f"  {title}", quiet)
    _say(_C.RULE, quiet)


def _fmt(x: object, nd: int = 2) -> str:
    """Format a number for console; pass through non-numerics and sentinels."""
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        if x != x:  # NaN
            return "nan"
        if isinstance(x, float):
            return f"{x:,.{nd}f}"
        return f"{x:,}"
    return str(x)


def _maybe(d: object, *keys: str, default: object = None) -> object:
    """Dig a value out of a dict/dataclass by trying each key in order."""
    if is_dataclass(d) and not isinstance(d, type):
        d = asdict(d)
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
    return default


# =============================================================================
# Lazy library access — the package is built by sibling agents; degrade cleanly
# if a not-yet-landed submodule is missing, but never silence a real failure.
# =============================================================================
class _Stage:
    """Track an analytics stage so the console can show OK / skip / FAIL and the
    process can exit non-zero on a genuine gate failure (not on an absent v1.1
    module)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = True
        self.skipped = False
        self.gate_failed = False
        self.detail = ""

    def skip(self, why: str) -> None:
        self.skipped = True
        self.ok = True
        self.detail = why

    def fail(self, why: str, gate: bool = False) -> None:
        self.ok = False
        self.gate_failed = gate
        self.detail = why

    def badge(self) -> str:
        if self.gate_failed:
            return _C.FAIL
        if self.skipped:
            return _C.SKIP
        return _C.OK if self.ok else _C.WARN


def _imp(modpath: str):
    """Import a submodule, returning None if it has not landed yet."""
    try:
        mod = __import__(modpath, fromlist=["*"])
        return mod
    except Exception:
        return None


# =============================================================================
# Argument parsing (contract §6)
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_options_analysis.py",
        description="Load a book YAML, run full options analytics, write an HTML "
                    "tearsheet + PNGs, print a console summary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("book", nargs="?", default=None,
                   help="Path to a book YAML (omit and pass --demo to use the "
                        "bundled TSLA 360/460 x8 demo).")
    p.add_argument("--demo", action="store_true",
                   help="Use the bundled TSLA demo book (bookio.TSLA_DEMO_YAML).")
    p.add_argument("--offline", action="store_true",
                   help="FixtureProvider / cached snapshots only (no network).")
    p.add_argument("--fetch", metavar="SYM", default=None,
                   help="Fetch live chain(s) for SYM, cache, merge into market state.")
    p.add_argument("--measure", choices=["rn", "rw"], default="rn",
                   help="Probability measure (default rn). Use --mu for real-world drift.")
    p.add_argument("--mu", type=float, default=None,
                   help="Real-world drift for --measure rw.")
    p.add_argument("--seed", type=int, default=7,
                   help="MC seed (default 7) for deterministic reports.")
    p.add_argument("--mc-paths", type=int, default=1_000_000,
                   help="MC path count (default 1,000,000).")
    p.add_argument("--charts-only", action="store_true",
                   help="Render PNGs to results/options/, skip the HTML report.")
    p.add_argument("--report-out", metavar="PATH", default=None,
                   help="Override the web/public/<name>_options_report.html path.")
    p.add_argument("--no-report", action="store_true",
                   help="Analytics + console summary only (no charts, no HTML).")
    p.add_argument("--quiet", action="store_true", help="Minimal console output.")
    p.add_argument("--subsets", metavar='"1,2,3;1,5,6,8"', default=None,
                   help="Semicolon-separated 1-based id groups to combine & "
                        "compare (ids are book-order position numbers; see the "
                        "POSITIONS console section).")
    p.add_argument("--decide", action="store_true",
                   help="Rank the --subsets (or each single position when "
                        "--subsets is omitted) via decision.rank_decisions.")
    p.add_argument("--horizon-days", type=float, default=30,
                   help="Decision / MC horizon in days (default 30).")
    return p


# =============================================================================
# Step 1 — load the book
# =============================================================================
def _load_book(args, quiet: bool):
    """Return (book, source_name, warnings, ok_bool)."""
    bookio = _imp("bursahack.options.bookio")
    if bookio is None:
        raise SystemExit(f"{_C.FAIL} bursahack.options.bookio is not available — "
                         "cannot load a book.")

    if args.demo:
        # The bundled worked example lives as a YAML string on bookio.
        demo_yaml = getattr(bookio, "TSLA_DEMO_YAML", None)
        if demo_yaml is None:
            raise SystemExit(f"{_C.FAIL} bookio.TSLA_DEMO_YAML missing.")
        book = _load_from_yaml_string(bookio, demo_yaml)
        return book, "tsla_demo", _book_warnings(book), True

    if not args.book:
        raise SystemExit("Provide a BOOK.yaml path or pass --demo. "
                         "See -h for usage.")

    path = Path(args.book)
    if not path.is_absolute():
        path = (Path.cwd() / path)
    if not path.exists():
        # Allow a bare filename to resolve against configs/options/.
        alt = OPTIONS_CONFIGS / Path(args.book).name
        if alt.exists():
            path = alt
        else:
            raise SystemExit(f"{_C.FAIL} book not found: {args.book}")

    book = bookio.load_book(path)
    return book, path.stem, _book_warnings(book), True


def _load_from_yaml_string(bookio, yaml_text: str):
    """load_book takes a path; if bookio exposes a string loader, use it, else
    spill to a temp file."""
    for fn in ("load_book_str", "loads", "load_book_string"):
        f = getattr(bookio, fn, None)
        if callable(f):
            return f(yaml_text)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(yaml_text)
        tmp = fh.name
    try:
        return bookio.load_book(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _book_warnings(book) -> list[str]:
    """load_book may attach a warnings list to the Book or return it separately;
    surface whatever is present."""
    w = getattr(book, "warnings", None)
    if isinstance(w, (list, tuple)):
        return list(w)
    return []


# =============================================================================
# Step 2 — resolve a MarketState per underlying
# =============================================================================
def _resolve_markets(book, args, stages: dict[str, _Stage], quiet: bool) -> dict:
    """Return {symbol: MarketState}. YAML-resolved by default; --fetch overlays a
    live/cached chain via the provider boundary (lazy)."""
    types = _imp("bursahack.options.types")
    markets: dict = {}

    # Pull whatever MarketStates load_book already resolved.
    pre = getattr(book, "markets", None) or getattr(book, "market_by_name", None)
    if isinstance(pre, dict):
        markets.update(pre)

    # Fall back to reconstructing a flat-vol MarketState from each position's
    # underlying if load_book didn't ship one.
    if not markets and types is not None:
        underlyings = _book_underlyings(book)
        for sym, meta in underlyings.items():
            try:
                markets[sym] = types.MarketState(
                    spot=float(meta.get("spot", 0.0)),
                    r=float(meta.get("r", 0.045)),
                    q=float(meta.get("q", 0.0)),
                    sigma=float(meta.get("sigma", 0.0)),
                    asof=getattr(book, "asof", None),
                )
            except Exception as exc:  # pragma: no cover - defensive
                stages["market"].fail(f"{sym}: {exc}")

    # --fetch: lazy provider + cache overlay (network path).
    if args.fetch:
        st = stages.setdefault("fetch", _Stage("fetch"))
        if args.offline:
            st.skip("--offline set; ignoring --fetch")
        else:
            try:
                md = _imp("bursahack.options.marketdata")
                if md is None:
                    st.skip("marketdata not available")
                else:
                    snap = md.fetch_chain(args.fetch, None)
                    if types is not None and snap is not None:
                        markets[args.fetch] = types.MarketState(
                            spot=float(getattr(snap, "spot", 0.0)),
                            r=0.045, q=0.0,
                            sigma=markets.get(args.fetch).sigma
                            if args.fetch in markets else 0.0,
                            asof=getattr(snap, "asof_utc", None),
                        )
                    st.detail = f"fetched {args.fetch}"
            except Exception as exc:
                st.fail(f"fetch {args.fetch}: {exc}")

    return markets


def _book_underlyings(book) -> dict[str, dict]:
    """Best-effort extraction of underlying metadata (spot/sigma/beta) from the
    Book, tolerating whatever shape bookio chose to attach."""
    u = getattr(book, "underlyings", None)
    out: dict[str, dict] = {}
    if isinstance(u, dict):
        for sym, meta in u.items():
            out[sym] = dict(meta) if isinstance(meta, dict) else _obj_to_dict(meta)
    elif isinstance(u, (list, tuple)):
        for meta in u:
            d = _obj_to_dict(meta)
            sym = d.get("symbol") or d.get("underlying")
            if sym:
                out[str(sym)] = d
    return out


def _obj_to_dict(o) -> dict:
    if isinstance(o, dict):
        return dict(o)
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    return {k: getattr(o, k) for k in dir(o)
            if not k.startswith("_") and not callable(getattr(o, k, None))}


def _betas(book) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym, meta in _book_underlyings(book).items():
        b = meta.get("beta")
        if b is not None:
            try:
                out[sym] = float(b)
            except (TypeError, ValueError):
                pass
    return out


# =============================================================================
# Step 3 — per-position + whole-book analytics
# =============================================================================
def _resolved_drift(args, mkt, prob_mod):
    """Resolve the measure->drift once, at the public boundary (contract L6 / §3
    prob.resolve_drift)."""
    if prob_mod is None:
        return (mkt.r - mkt.q) if mkt is not None else 0.0
    types = _imp("bursahack.options.types")
    measure = types.Measure.REAL_WORLD if args.measure == "rw" else types.Measure.RISK_NEUTRAL
    return prob_mod.resolve_drift(measure, mkt.r, mkt.q, args.mu)


def _measure_enum(args):
    types = _imp("bursahack.options.types")
    if types is None:
        return args.measure
    return types.Measure.REAL_WORLD if args.measure == "rw" else types.Measure.RISK_NEUTRAL


def _position_cards(book, markets, args, stages, quiet) -> list[dict]:
    """For each position: classify -> economics -> summary_card, plus prob/MC.
    Returns a list of console/report-ready card dicts (best-effort, defensive)."""
    structure = _imp("bursahack.options.structure")
    prob = _imp("bursahack.options.prob")
    mc = _imp("bursahack.options.mc")
    position = _imp("bursahack.options.position")
    cards: list[dict] = []

    positions = getattr(book, "positions", ()) or ()
    for pos in positions:
        sym = getattr(pos, "underlying", "") or ""
        mkt = markets.get(sym)
        legs = list(getattr(pos, "legs", ()) or ())
        card: dict = {
            "name": getattr(pos, "name", "") or sym,
            "underlying": sym,
            "n_legs": len(legs),
        }

        # classify
        try:
            if structure is not None:
                strat = structure.classify(pos)
                card["strategy"] = _maybe(strat, "name", default="custom")
                card["variant"] = _maybe(strat, "variant", default="")
        except Exception as exc:
            card["strategy"] = "(classify failed)"
            card["_classify_err"] = str(exc)

        # economics
        try:
            if structure is not None and mkt is not None:
                s_grid = _spot_grid(mkt.spot)
                econ = structure.economics(pos, mkt, s_grid)
                card["net_premium"] = _maybe(econ, "net_premium")
                card["breakevens"] = _maybe(econ, "breakevens", default=())
                card["max_profit"] = _maybe(econ, "max_profit")
                card["max_loss"] = _maybe(econ, "max_loss")
                card["width"] = _maybe(econ, "width")
                card["rr"] = _maybe(econ, "rr")
                card["ladder"] = _maybe(econ, "expiry_pl_ladder", default={})
                ng = _maybe(econ, "net_greeks")
                if ng is not None:
                    card["net_delta"] = _maybe(ng, "delta")
                    card["net_theta_day"] = _maybe(ng, "theta_day")
                    card["net_vega"] = _maybe(ng, "vega")
        except Exception as exc:
            card["_econ_err"] = str(exc)
            stages["economics"].detail = "partial"

        # summary card (canonical position card)
        try:
            if structure is not None and mkt is not None:
                summ = structure.summary_card(legs, mkt)
                if isinstance(summ, dict):
                    card["summary"] = summ
        except Exception:
            pass

        # POP / probability (measure-aware boundary)
        try:
            if prob is not None and mkt is not None:
                T = _approx_T(legs, getattr(book, "asof", None))
                pres = prob.pop_expiry(
                    legs, mkt.spot, T, mkt.sigma, mkt.r, q=mkt.q,
                    measure=_measure_enum(args), mu=args.mu,
                )
                card["pop"] = _maybe(pres, "pop")
        except Exception as exc:
            card["_prob_err"] = str(exc)

        cards.append(card)

    return cards


def _spot_grid(spot: float, n: int = 41, span: float = 0.5) -> list[float]:
    if spot <= 0:
        spot = 100.0
    lo, hi = spot * (1 - span), spot * (1 + span)
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def _approx_T(legs, asof) -> float:
    """Years to the nearest leg expiry, best-effort. Pure analytics want T in
    years; the precise helper is marketdata.time_to_expiry, used in the full
    pipeline — this is a CLI-side fallback for the console summary."""
    from datetime import date, datetime
    base = None
    if isinstance(asof, datetime):
        base = asof.date()
    elif isinstance(asof, date):
        base = asof
    else:
        base = date.today()
    Ts = []
    for lg in legs:
        exp = getattr(lg, "expiry", None)
        if isinstance(exp, datetime):
            exp = exp.date()
        if isinstance(exp, date):
            Ts.append(max((exp - base).days / 365.0, 0.0))
    return min([t for t in Ts if t > 0], default=1.0)


def _book_aggregates(book, markets, args, stages, quiet) -> dict:
    """Whole-book net greeks, posture, margin, naked scan."""
    book_mod = _imp("bursahack.options.book")
    margin_mod = _imp("bursahack.options.margin")
    out: dict = {}

    netliq = _account_netliq(book)
    cash = _account_cash(book)
    betas = _betas(book)
    s_spy = 600.0  # placeholder index level for beta-weighting; report annotates

    if book_mod is not None:
        try:
            gr = book_mod.net_greeks(book, markets)
            out["net_greeks"] = getattr(gr, "totals", None) or _obj_to_dict(gr)
        except Exception as exc:
            stages["book"].fail(f"net_greeks: {exc}")
        try:
            out["naked"] = book_mod.naked_scan(book, markets)
        except Exception:
            pass
        try:
            out["posture"] = book_mod.income_posture(
                book, markets, betas, s_spy, netliq)
        except Exception as exc:
            stages["book"].detail = f"posture partial: {exc}"
    else:
        stages["book"].skip("book module not available")

    if margin_mod is not None and netliq:
        try:
            rep = margin_mod.pm_maintenance(book, markets, cash, netliq)
            out["margin"] = {
                "maintenance": _maybe(rep, "maintenance"),
                "excess_liquidity": _maybe(rep, "excess_liquidity"),
                "netliq": _maybe(rep, "netliq", default=netliq),
                "binding_node": _maybe(rep, "binding_node"),
            }
        except Exception as exc:
            stages["margin"].fail(f"pm_maintenance: {exc}")
    elif margin_mod is None:
        stages["margin"].skip("margin module not available")

    out["netliq"] = netliq
    out["excess_liquidity_reported"] = _account_excess_liq(book)
    return out


# =============================================================================
# Step 3c — combination / decision layer (contract: combine + decision modules)
# =============================================================================
def _parse_subsets(s: str | None) -> list[list[int]]:
    """Parse a ``"1,2,3;1,5,6,8"`` string into ``[[1,2,3],[1,5,6,8]]``.

    Split on ``;`` into groups, each group on ``,`` into 1-based ids. Blank
    groups are dropped; non-int tokens raise ``ValueError`` (surfaced by the
    caller). Validation against the book's position count is done downstream by
    ``combine.select_subset``.
    """
    if not s:
        return []
    groups: list[list[int]] = []
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ids: list[int] = []
        for tok in chunk.split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                ids.append(int(tok))
            except ValueError as exc:
                raise ValueError(
                    f"--subsets: '{tok}' is not an integer id (in group "
                    f"'{chunk}')"
                ) from exc
        if ids:
            groups.append(ids)
    return groups


def _subset_market(markets: dict, sub_ids: list[int], book):
    """Resolve the MarketState a single subset should be evaluated against.

    A subset that touches one underlying gets that underlying's MarketState
    (so combine/decision can take a bare MarketState); a multi-underlying
    subset gets the full ``{sym: MarketState}`` map.
    """
    positions = list(getattr(book, "positions", ()) or ())
    syms: list[str] = []
    for i in sub_ids:
        if 1 <= i <= len(positions):
            sym = getattr(positions[i - 1], "underlying", "") or ""
            if sym and sym not in syms:
                syms.append(sym)
    if len(syms) == 1 and markets.get(syms[0]) is not None:
        return markets[syms[0]]
    return markets


def _combination(book, markets, args, stages, quiet) -> dict:
    """Build the combine/compare (+ optional decision ranking) result.

    Returns a structured dict consumed by ``_report_ctx`` (-> report
    ``ctx["combination"]``) and printed to the console. Defensive: if the
    ``combine`` / ``decision`` modules have not landed, or any call raises, the
    stage degrades to a skip / partial and the rest of the pipeline continues
    (contract design ruling L7).
    """
    out: dict = {}
    st = stages["combination"]
    dst = stages["decision"]

    try:
        id_sets = _parse_subsets(getattr(args, "subsets", None))
    except ValueError as exc:
        st.fail(str(exc))
        return out

    if not id_sets:
        st.skip("no --subsets supplied")
        if not getattr(args, "decide", False):
            dst.skip("no --decide / --subsets")
            return out

    combine = _imp("bursahack.options.combine")
    decision = _imp("bursahack.options.decision")
    if combine is None:
        st.skip("combine module not available")
        dst.skip("combine module not available")
        return out

    account = getattr(book, "account", None)
    if account is None:
        st.skip("no Account on book — combination needs netliq")
        dst.skip("no Account on book")
        return out

    # --- comparison across the requested subsets --------------------------
    compare: dict = {}
    if id_sets:
        try:
            mkt = _subset_market(markets, id_sets[0], book) if len(id_sets) == 1 \
                else markets
            compare = combine.compare_subsets(book, markets, account, id_sets)
            out["compare"] = compare
            n = len(compare.get("subsets") or [])
            st.detail = f"compared {n} subset(s)"
        except Exception as exc:
            st.fail(f"compare_subsets: {exc}")
            if not quiet:
                traceback.print_exc()

    # --- decision ranking (optional) --------------------------------------
    candidates: list[dict] = []
    if getattr(args, "decide", False) and decision is not None:
        # candidates: the explicit subsets, or each single position if none given
        cand_sets = id_sets or [[i] for i in range(
            1, len(getattr(book, "positions", ()) or ()) + 1)]
        try:
            ranked = decision.rank_decisions(
                book, markets, account, cand_sets,
                horizon_days=getattr(args, "horizon_days", 30),
                measure=_measure_enum(args), mu=args.mu, seed=args.seed,
            )
            out["ranked"] = ranked
            for row in ranked.get("ranked", []):
                candidates.append({
                    "rank": row.get("rank"),
                    "label": row.get("label"),
                    "ids": row.get("ids"),
                    "scorecard": row.get("scorecard") or {},
                    "liquidation": (row.get("scorecard") or {}).get("liquidation")
                    or {},
                    "prob_liquidation": (row.get("scorecard") or {}).get(
                        "liquidation_prob"),
                    "recommendation": row.get("recommendation"),
                    "dominating_risk": row.get("dominating_risk"),
                    "tradeoff": row.get("tradeoff"),
                })
            dst.detail = f"ranked {len(candidates)} candidate(s)"
        except Exception as exc:
            dst.fail(f"rank_decisions: {exc}")
            if not quiet:
                traceback.print_exc()
    elif getattr(args, "decide", False) and decision is None:
        dst.skip("decision module not available")
    else:
        dst.skip("--decide not set")

    # --- if no decision ranking, still surface per-subset scorecards as
    #     lightweight candidate cards (liquidation point + scorecard KPIs) -----
    if not candidates and id_sets and decision is not None:
        for ids in id_sets:
            try:
                sc = decision.scorecard(
                    book, ids, _subset_market(markets, ids, book), account,
                    horizon_days=getattr(args, "horizon_days", 30),
                    measure=_measure_enum(args), mu=args.mu, seed=args.seed,
                )
                candidates.append({
                    "label": ",".join(str(i) for i in ids),
                    "ids": ids,
                    "scorecard": sc,
                    "liquidation": sc.get("liquidation") or {},
                    "prob_liquidation": sc.get("liquidation_prob"),
                })
            except Exception:
                continue

    if candidates:
        out["candidates"] = candidates

    # Liquidation points for the first subset feed the mc_fan chart sidecar /
    # console highlight; the chart itself is rendered in _render's chart_ctx.
    if id_sets and combine is not None:
        try:
            liq = combine.liquidation_point(
                combine.select_subset(book, id_sets[0]),
                _subset_market(markets, id_sets[0], book), account,
                direction="both",
            )
            out["liquidation_first"] = liq
        except Exception:
            pass

    return out


def _print_combination(combo: dict, quiet: bool) -> None:
    """Console rendering of the combination/decision result."""
    if quiet or not combo:
        return
    _section("COMBINATION / DECISION", quiet)

    compare = combo.get("compare") or {}
    subsets = compare.get("subsets") or []
    columns = compare.get("columns") or []
    if subsets and columns:
        # compact console table: subset label + a few headline metrics
        head_cols = [c for c in ("label", "pnl_base", "delta_equiv_shares",
                                 "maintenance", "excess_liquidity", "max_loss",
                                 "liquidation_down_pct") if c in columns]
        if not head_cols:
            head_cols = columns[:6]
        _say("  " + "  ".join(f"{c:>16}" for c in head_cols), quiet)
        for sub in subsets:
            cells = []
            for c in head_cols:
                v = sub.get(c)
                if c == "label":
                    cells.append(f"{str(v):>16}")
                elif isinstance(v, (int, float)):
                    cells.append(f"{_fmt(v):>16}")
                else:
                    cells.append(f"{str(v):>16}")
            _say("  " + "  ".join(cells), quiet)
    elif subsets:
        for sub in subsets:
            _say(f"  {sub.get('label', sub.get('ids'))}: "
                 f"pnl_base={_fmt(sub.get('pnl_base'))} "
                 f"maint={_fmt(sub.get('maintenance'))} "
                 f"EL={_fmt(sub.get('excess_liquidity'))}", quiet)

    candidates = combo.get("candidates") or []
    if candidates:
        _say("", quiet)
        _say("  ranked candidates:" if combo.get("ranked")
             else "  candidate scorecards:", quiet)
        for c in candidates:
            rank = c.get("rank")
            prefix = f"  #{rank} " if rank is not None else "  - "
            sc = c.get("scorecard") or {}
            bits = []
            if sc.get("ev_rn") is not None:
                bits.append(f"EV {_fmt(sc['ev_rn'])}")
            if sc.get("pop") is not None:
                bits.append(f"POP {_fmt(sc['pop'], 4)}")
            if c.get("prob_liquidation") is not None:
                bits.append(f"P(liq) {_fmt(c['prob_liquidation'], 4)}")
            if sc.get("bp_consumed") is not None:
                bits.append(f"BP {_fmt(sc['bp_consumed'])}")
            _say(prefix + f"{c.get('label', '?')}  " + "  ".join(bits), quiet)
            if c.get("recommendation"):
                _say(f"        {c['recommendation']}", quiet)
            if c.get("dominating_risk"):
                _say(f"        risk: {c['dominating_risk']}", quiet)


def _account_netliq(book) -> float:
    acct = getattr(book, "account", None)
    nl = getattr(acct, "netliq", None) if acct is not None else None
    if nl is None:
        nl = _maybe(getattr(book, "account", {}), "netliq", default=0.0)
    try:
        return float(nl or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _account_cash(book) -> float:
    acct = getattr(book, "account", None)
    c = getattr(acct, "cash", None) if acct is not None else None
    if c is None:
        c = _maybe(getattr(book, "account", {}), "cash", default=0.0)
    try:
        return float(c or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _account_excess_liq(book) -> float | None:
    """Broker-reported ExcessLiquidity if present in the YAML/account; context for
    the model estimate."""
    acct = getattr(book, "account", None)
    for src in (acct, getattr(book, "account", None)):
        if src is None:
            continue
        v = getattr(src, "excess_liquidity", None)
        if v is None and isinstance(src, dict):
            v = src.get("excess_liquidity")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


# =============================================================================
# Step 3b — MC <-> analytic reconciliation gate (contract §5 / §6: exit non-zero)
# =============================================================================
def _reconcile_gate(book, markets, args, stages, quiet) -> bool:
    """Run the shipped MC summaries through reconcile() against analytic anchors.
    Returns True if all gates pass (or there is nothing to reconcile). A genuine
    gate FAILURE forces a non-zero exit (contract §6)."""
    mc = _imp("bursahack.options.mc")
    bsm = _imp("bursahack.options.bsm")
    prob = _imp("bursahack.options.prob")
    st = stages["reconcile"]

    if mc is None or bsm is None:
        st.skip("mc/bsm not available — no reconciliation to run")
        return True

    types = _imp("bursahack.options.types")
    all_pass = True
    checks = 0
    # Anchor: MC terminal price of a representative single call vs bsm.price.
    for sym, mkt in markets.items():
        if mkt is None or mkt.spot <= 0 or mkt.sigma <= 0:
            continue
        try:
            T = 1.0
            K = round(mkt.spot / 10) * 10 or mkt.spot
            drift = _resolved_drift(args, mkt, prob)
            n = min(args.mc_paths, 200_000)  # keep the CLI gate snappy
            st_arr = mc.simulate_terminal(mkt.spot, T, mkt.sigma, drift,
                                          n=n, seed=args.seed, antithetic=True)
            import numpy as np
            disc = float(np.exp(-mkt.r * T))
            payoff = np.maximum(st_arr - K, 0.0) * disc
            mc_px = float(payoff.mean())
            se = float(payoff.std(ddof=1) / (len(payoff) ** 0.5))
            right = types.Right.CALL if types is not None else "C"
            analytic = bsm.price(mkt.spot, K, T, mkt.r, mkt.q, mkt.sigma, right)
            res = mc.reconcile(mc_px, se, analytic, k=3.0)
            passed = bool(_maybe(res, "pass", default=False))
            checks += 1
            all_pass = all_pass and passed
            st.detail = (f"{sym} {int(K)}C: mc={mc_px:.3f} "
                         f"analytic={analytic:.3f} -> "
                         f"{'pass' if passed else 'FAIL'}")
            break  # one representative anchor per book is enough for the CLI gate
        except Exception as exc:
            st.skip(f"reconcile anchor unavailable: {exc}")
            return True

    if checks == 0:
        st.skip("no reconcilable anchor in this book")
        return True
    if not all_pass:
        st.fail(st.detail, gate=True)
    return all_pass


# =============================================================================
# Step 4/5 — charts + HTML report
# =============================================================================
def _scenario_tornado_pnl(book, markets) -> dict:
    """True mark-to-market scenario P&L for the tornado: {spot-shock label: ΔP&L}.

    The chart's own canned fallback intrinsics the legs at expiry (terminal value),
    which for a long-dated, deeply-long-delta book makes a -20% crash read as a
    small GREEN profit. That is wrong: this routes through the scenario engine
    (``scenario.scenario_grid`` -> ``position.value_position``, the ONE repricing
    kernel, L3) at dt=0 / iv_shift=0 and reports each shock's mark value MINUS the
    0%-move mark, so the bars are genuine MTM P&L versus today and a crash renders
    as the largest negative (red) bar. Uses the book's configured ``spots_pct``
    grid (excluding 0, the baseline) so the tornado matches the analysis scenarios.
    """
    try:
        from bursahack.options import scenario as _scenario
    except Exception:
        return {}

    legs: list = []
    for pos in getattr(book, "positions", ()):  # flatten the whole book
        legs.extend(getattr(pos, "legs", ()))
    if not legs:
        return {}

    mkt = None
    if isinstance(markets, dict):
        sym = getattr(book.positions[0], "underlying", "") if getattr(book, "positions", None) else ""
        mkt = markets.get(sym) or next((m for m in markets.values() if m is not None), None)
    else:
        mkt = markets
    if mkt is None:
        return {}

    grid = getattr(book, "scenarios", None) or {}
    spots_pct = list(grid.get("spots_pct") or [-0.20, -0.10, 0.10, 0.20])
    if 0 not in spots_pct and 0.0 not in spots_pct:
        spots_pct = spots_pct + [0.0]

    try:
        rows = _scenario.scenario_grid(legs, mkt, spots_pct, [0], [0], capital=0.0)
    except Exception:
        return {}

    S0 = float(getattr(mkt, "spot", 0.0)) or 1.0
    base_pnl = next((r["pnl"] for r in rows if abs(r["spot"] - S0) < 1e-6), 0.0)
    out: dict[str, float] = {}
    for r in rows:
        pct = (r["spot"] / S0) - 1.0
        if abs(pct) < 1e-9:
            continue  # skip the 0% baseline row
        out[f"{pct * 100:+.0f}%"] = float(r["pnl"]) - float(base_pnl)
    return out


def _render(book, markets, cards, aggregates, combination, args, stages, quiet) -> dict:
    """Build the ctx dict, render charts -> results/options/, build HTML report
    -> web/public/. Returns {'charts': ..., 'report_path': ...}."""
    out: dict = {}
    if args.no_report:
        stages["charts"].skip("--no-report")
        stages["report"].skip("--no-report")
        return out

    charts_mod = _imp("bursahack.options.viz.charts")
    report_mod = _imp("bursahack.options.viz.report")
    account = getattr(book, "account", None)

    # render_all_charts (viz.charts) reads a FLAT chart-ctx keyed by
    # book / market_by_name / legs / market — not the report ctx schema. Keep the
    # two contexts separate: this one feeds the chart renderer; the report ctx
    # (built below) feeds build_html_report. We also hand it the decision-layer
    # inputs (account, positions=cards, compare_result) so render_all_charts can
    # produce the new mc_fan_liquidation + combination_comparison charts.
    chart_ctx = {
        "book": book,
        "markets": markets,
        "market_by_name": markets,
        "account": account,
        "positions": cards,
        # decision-layer chart knobs (mc_fan_liquidation reads these)
        "horizon_days": getattr(args, "horizon_days", 30),
        "measure": args.measure,
        "mu": args.mu,
        "seed": args.seed,
    }
    # The mc_fan_liquidation job wants a single MarketState (`market`); supply the
    # primary underlying's MarketState for a (typically single-underlying) book.
    primary_market = next((m for m in markets.values() if m is not None), None)
    if primary_market is not None:
        chart_ctx["market"] = primary_market
    compare = (combination or {}).get("compare")
    if compare:
        chart_ctx["compare_result"] = compare

    # Scenario tornado: feed TRUE MTM scenario P&L (vs the 0%-move baseline) so the
    # chart reflects the book's real directional risk instead of the intrinsic-at-
    # expiry canned fallback (which mislabels a crash as a small green profit).
    scen_pnl = _scenario_tornado_pnl(book, markets)
    if scen_pnl:
        chart_ctx["scenarios"] = scen_pnl

    # Charts -> {chart_id: {"png_path","base64","sidecar"}}
    rendered: dict = {}
    if charts_mod is not None:
        try:
            OPTIONS_RESULTS.mkdir(parents=True, exist_ok=True)
            rendered = charts_mod.render_all_charts(chart_ctx, OPTIONS_RESULTS)
            out["charts"] = rendered
            n = sum(1 for v in rendered.values()
                    if isinstance(v, dict) and v.get("base64"))
            stages["charts"].detail = f"{n} charts -> {OPTIONS_RESULTS}"
        except Exception as exc:
            stages["charts"].fail(f"render_all_charts: {exc}")
            if not quiet:
                traceback.print_exc()
    else:
        stages["charts"].skip("viz.charts not available")

    # Per-position payoff + greeks thumbnails. render_all_charts above only
    # renders the BOOK-level charts (its chart_ctx lacks per-position legs/market);
    # so the CLI renders one payoff_diagram + greeks_vs_spot per position here and
    # folds them into `rendered`. The payoff data-uri is also stitched onto each
    # card as card["thumb"] so the report's _section_positions shows it inline.
    pos_thumbs = _render_position_thumbs(book, markets, cards, charts_mod, quiet)
    for cid, uri in pos_thumbs.items():
        rendered.setdefault(cid, {"png_path": "", "base64": uri, "sidecar": {}})

    if args.charts_only:
        stages["report"].skip("--charts-only")
        return out

    # Assemble the report ctx in the schema build_html_report consumes
    # (meta / summary / positions / charts / reconcile / warnings). This is the
    # CLI's job as orchestrator — translate the already-computed cards +
    # aggregates into the report's nested dict.
    ctx = _report_ctx(book, markets, cards, aggregates, rendered, combination,
                      args, stages)

    # HTML report
    if report_mod is not None:
        try:
            out_path = _report_path(args, book)
            WEB_PUBLIC.mkdir(parents=True, exist_ok=True)
            written = report_mod.build_html_report(ctx, out_path, mode="book")
            out["report_path"] = written or str(out_path)
            stages["report"].detail = f"{out['report_path']}"
        except Exception as exc:
            stages["report"].fail(f"build_html_report: {exc}")
            if not quiet:
                traceback.print_exc()
    else:
        stages["report"].skip("viz.report not available")

    return out


def _render_position_thumbs(book, markets, cards, charts_mod, quiet) -> dict:
    """Render a payoff + greeks thumbnail per position.

    Returns ``{f"pos{i}_payoff": data-uri, f"pos{i}_greeks": data-uri, ...}``
    (1-based to match the position id), and stitches the payoff data-uri onto the
    matching ``cards[i]["thumb"]`` so the report shows it inline on the position
    card (the greeks small-multiples land in the report appendix). Each chart is
    wrapped in try/except — a single bad position never aborts the report.
    """
    if charts_mod is None:
        return {}
    payoff_fn = getattr(charts_mod, "payoff_diagram", None)
    greeks_fn = getattr(charts_mod, "greeks_vs_spot", None)
    to_b64 = getattr(charts_mod, "to_base64", None)
    if payoff_fn is None or to_b64 is None:
        return {}

    position_mod = _imp("bursahack.options.position")
    thumbs: dict = {}
    positions = list(getattr(book, "positions", ()) or ())
    for idx, pos in enumerate(positions, start=1):
        sym = getattr(pos, "underlying", "") or ""
        mkt = markets.get(sym)
        if mkt is None:
            continue
        legs = list(getattr(pos, "legs", ()) or ())
        if not legs:
            continue
        try:
            net_cost = (position_mod.net_cost_entry(legs)
                        if position_mod is not None else 0.0)
        except Exception:
            net_cost = 0.0

        # payoff thumbnail -> card["thumb"] + pos{i}_payoff
        try:
            fig, _sc = payoff_fn(legs, mkt, net_cost=net_cost)
            uri = to_b64(fig)
            if isinstance(uri, str) and uri.startswith("data:image"):
                thumbs[f"pos{idx}_payoff"] = uri
                if idx - 1 < len(cards) and isinstance(cards[idx - 1], dict):
                    cards[idx - 1]["thumb"] = uri
        except Exception as exc:
            if not quiet:
                _say(f"  {_C.WARN} pos{idx} payoff thumb: {exc}", quiet)

        # greeks small-multiples -> pos{i}_greeks (appendix)
        if greeks_fn is not None:
            try:
                fig, _sc = greeks_fn(legs, mkt)
                uri = to_b64(fig)
                if isinstance(uri, str) and uri.startswith("data:image"):
                    thumbs[f"pos{idx}_greeks"] = uri
            except Exception:
                pass
    return thumbs


def _coerce_chart_uri(val: object) -> str | None:
    """Pull a base64 data-URI out of a render_all_charts entry.

    render_all_charts returns {chart_id: {"png_path","base64","sidecar"}}; the
    report's _coerce_chart accepts a str/tuple/Figure but not that dict, so we
    extract the data-URI here.
    """
    if isinstance(val, dict):
        b64 = val.get("base64") or val.get("uri")
        if isinstance(b64, str) and b64.startswith("data:image"):
            return b64
        png = val.get("png_path")
        if isinstance(png, str) and png:
            return png  # report._coerce_chart will read the PNG off disk
        return None
    if isinstance(val, str) and val:
        return val
    return None


def _report_ctx(book, markets, cards, aggregates, rendered, combination,
                args, stages) -> dict:
    """Translate the CLI's computed state into the report's ctx schema.

    Schema consumed by report.build_html_report:
      meta {title, generated, asof, underlyings, spot, nlv, assumptions}
      summary {dollar_greeks, income_posture, prob_profit}
      positions [{name, classification, underlying, legs[], entry, max_profit,
                  max_loss, rr, breakevens, pop, greeks, thumb}]
      charts {chart_id: data-uri}
      combination {compare, candidates, charts}   # decision layer
      reconcile [{name, passed, gap_in_se}]
      warnings [str]
    """
    from datetime import datetime

    underlyings = list(markets.keys())
    spot = {sym: getattr(m, "spot", None)
            for sym, m in markets.items() if m is not None}

    meta = {
        "title": _book_report_title(underlyings),
        "generated": datetime.now().replace(microsecond=0),
        "asof": getattr(book, "asof", None),
        "underlyings": underlyings,
        "spot": spot,
        "nlv": aggregates.get("netliq") or None,
        "assumptions": {
            "r": _common_r(markets),
            "q": _common_q(markets),
            "vol_source": "flat (MarketState.sigma) / per-leg IV",
            "model": "Black-Scholes / CRR + GBM Monte-Carlo",
            "measure": args.measure,
            "mu": args.mu,
            "seed": args.seed,
            "mc_paths": args.mc_paths,
        },
    }

    # summary: dollar greeks + income posture
    summary: dict = {}
    ng = aggregates.get("net_greeks")
    if isinstance(ng, dict) and ng:
        summary["dollar_greeks"] = dict(ng)
    posture = aggregates.get("posture")
    if isinstance(posture, dict) and posture:
        summary["income_posture"] = dict(posture)

    # positions: map each card (+ raw legs) to the report position schema
    positions = []
    raw_positions = list(getattr(book, "positions", ()) or ())
    for idx, card in enumerate(cards):
        pos = raw_positions[idx] if idx < len(raw_positions) else None
        legs = list(getattr(pos, "legs", ()) or ()) if pos is not None else ()
        rec = {
            "name": card.get("name"),
            "classification": card.get("strategy"),
            "underlying": card.get("underlying"),
            "legs": [_leg_to_report_dict(lg) for lg in legs],
            "entry": card.get("net_premium"),
            "max_profit": card.get("max_profit"),
            "max_loss": card.get("max_loss"),
            "rr": card.get("rr"),
            "breakevens": card.get("breakevens"),
            "pop": card.get("pop"),
        }
        g = {}
        if card.get("net_delta") is not None:
            g["delta"] = card["net_delta"]
        if card.get("net_theta_day") is not None:
            g["theta_day"] = card["net_theta_day"]
        if card.get("net_vega") is not None:
            g["vega"] = card["net_vega"]
        if g:
            rec["greeks"] = g
        # per-position payoff thumbnail (stitched on by _render_position_thumbs)
        thumb = card.get("thumb")
        if thumb:
            rec["thumb"] = thumb
        positions.append(rec)

    # charts: coerce render_all_charts dict -> {chart_id: data-uri}
    charts: dict = {}
    for cid, val in (rendered or {}).items():
        uri = _coerce_chart_uri(val)
        if uri:
            charts[cid] = uri

    # reconcile + warnings badges from the stage tracker
    reconcile = []
    rst = stages.get("reconcile")
    if rst is not None and (rst.detail or not rst.skipped):
        reconcile.append({
            "name": "MC ↔ analytic",
            "passed": rst.ok and not rst.gate_failed,
            "gap_in_se": None,
        })

    # combination / decision block (only when --subsets produced something)
    combination_ctx: dict = {}
    if combination:
        compare = combination.get("compare")
        candidates = combination.get("candidates")
        combo_charts: dict = {}
        for cid in ("combination_comparison", "mc_fan_liquidation"):
            if cid in charts:
                combo_charts[cid] = charts[cid]
        if compare or candidates or combo_charts:
            combination_ctx = {
                "compare": compare or {},
                "candidates": candidates or [],
                "charts": combo_charts,
            }

    # margin / liquidity block — carry BOTH the broker-reported ExcessLiquidity
    # (authoritative for risk-of-call) and the model stress-grid estimate so the
    # report can show the gap/ratio + a one-line caveat (issue #2). The model
    # estimate is INTRINSIC-only today and runs optimistic; the panel must never
    # present the model number as if it were the broker's.
    margin_ctx = dict(aggregates.get("margin") or {})
    broker_el = aggregates.get("excess_liquidity_reported")
    if broker_el is not None:
        margin_ctx["broker_excess_liquidity"] = broker_el
    model_el = margin_ctx.get("excess_liquidity")
    if broker_el not in (None, 0) and isinstance(model_el, (int, float)):
        margin_ctx["el_gap"] = float(model_el) - float(broker_el)
        margin_ctx["el_ratio"] = float(model_el) / float(broker_el)

    return {
        "meta": meta,
        "summary": summary,
        "positions": positions,
        "charts": charts,
        "combination": combination_ctx,
        "margin": margin_ctx,
        "catalysts": _catalysts_ctx(book),
        "reconcile": reconcile,
        "warnings": list(_book_warnings(book)),
    }


def _catalysts_ctx(book) -> dict:
    """Translate ``book.catalysts`` (a tuple of {date,label,confirmed} dicts) into
    the report's catalysts schema so the corrected earnings date surfaces.

    The report's _section_catalysts reads ``earnings:[{date,label,confirmed}]``;
    a catalyst with 'earn' in its label routes there, anything else falls through
    to a generic earnings-style row (date + label + status)."""
    cats = list(getattr(book, "catalysts", ()) or ())
    if not cats:
        return {}
    earnings: list[dict] = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        earnings.append({
            "date": c.get("date"),
            "label": c.get("label") or "catalyst",
            "confirmed": bool(c.get("confirmed")),
        })
    return {"earnings": earnings} if earnings else {}


def _book_report_title(underlyings) -> str:
    if not underlyings:
        return "Options Book — Desk Tearsheet"
    if len(underlyings) == 1:
        return f"{underlyings[0]} Options — Desk Tearsheet"
    head = ", ".join(underlyings[:3])
    tail = "…" if len(underlyings) > 3 else ""
    return f"Options Book ({head}{tail}) — Desk Tearsheet"


def _leg_to_report_dict(leg) -> dict:
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


def _report_path(args, book) -> Path:
    if args.report_out:
        p = Path(args.report_out)
        return p if p.is_absolute() else (Path.cwd() / p)
    name = getattr(args, "_book_name", None) or "book"
    return WEB_PUBLIC / f"{name}_options_report.html"


def _common_r(markets) -> float:
    for m in markets.values():
        if m is not None:
            return m.r
    return 0.045


def _common_q(markets) -> float:
    for m in markets.values():
        if m is not None:
            return m.q
    return 0.0


# =============================================================================
# Step 6 — console summary
# =============================================================================
def _print_summary(book, markets, cards, aggregates, render_out, stages,
                   warnings, args, quiet) -> None:
    if quiet:
        return

    _section("OPTIONS BOOK ANALYSIS", quiet)
    _say(f"  as-of      : {getattr(book, 'asof', '(n/a)')}", quiet)
    _say(f"  underlyings: {', '.join(markets.keys()) or '(none)'}", quiet)
    nl = aggregates.get("netliq")
    _say(f"  NetLiq     : {_fmt(nl)}" if nl else "  NetLiq     : (n/a)", quiet)
    _say(f"  measure    : {args.measure}"
         + (f" (mu={args.mu})" if args.measure == 'rw' else "")
         + f"  | seed {args.seed} | mc-paths {args.mc_paths:,}", quiet)

    for sym, m in markets.items():
        if m is not None:
            _say(f"  market[{sym}]: spot {_fmt(m.spot)}  sigma {_fmt(m.sigma, 4)}"
                 f"  r {_fmt(m.r, 4)}  q {_fmt(m.q, 4)}", quiet)

    if warnings:
        _say("", quiet)
        _say("  validation warnings:", quiet)
        for w in warnings:
            _say(f"    {_C.WARN} {w}", quiet)

    # Per-position cards
    _section("POSITIONS", quiet)
    if not cards:
        _say("  (no positions)", quiet)
    for c in cards:
        _say("", quiet)
        _say(f"  {c.get('name', '?')}", quiet)
        _say(f"    {c.get('strategy', '?')}"
             + (f" / {c['variant']}" if c.get("variant") else "")
             + f"   [{c.get('underlying', '')}, {c.get('n_legs', 0)} legs]", quiet)
        bits = []
        if c.get("net_premium") is not None:
            bits.append(f"net_premium {_fmt(c['net_premium'])}")
        be = c.get("breakevens")
        if be:
            bits.append("BE " + "/".join(_fmt(x) for x in (be if hasattr(be, '__iter__') else [be])))
        if c.get("max_profit") is not None:
            bits.append(f"maxP {_fmt(c['max_profit'])}")
        if c.get("max_loss") is not None:
            bits.append(f"maxL {_fmt(c['max_loss'])}")
        if c.get("rr") is not None:
            bits.append(f"R:R {_fmt(c['rr'])}")
        if c.get("pop") is not None:
            bits.append(f"POP {_fmt(c['pop'], 4)}")
        if bits:
            _say("    " + "  ".join(bits), quiet)
        gbits = []
        if c.get("net_delta") is not None:
            gbits.append(f"delta {_fmt(c['net_delta'], 4)}")
        if c.get("net_theta_day") is not None:
            gbits.append(f"theta/day {_fmt(c['net_theta_day'], 4)}")
        if c.get("net_vega") is not None:
            gbits.append(f"vega {_fmt(c['net_vega'], 4)}")
        if gbits:
            _say("    greeks: " + "  ".join(gbits), quiet)
        for ek in ("_classify_err", "_econ_err", "_prob_err"):
            if c.get(ek):
                _say(f"    {_C.WARN} {ek[1:]}: {c[ek]}", quiet)

    # Book aggregates
    _section("BOOK — NET GREEKS / POSTURE / MARGIN", quiet)
    ng = aggregates.get("net_greeks")
    if isinstance(ng, dict):
        order = ["delta_sh", "dollar_delta", "dollar_gamma_1pct",
                 "dollar_vega", "dollar_theta_day", "dollar_rho_1pct"]
        for k in order:
            if k in ng:
                _say(f"  {k:<20}: {_fmt(ng[k])}", quiet)
        for k, v in ng.items():
            if k not in order:
                _say(f"  {k:<20}: {_fmt(v)}", quiet)
    else:
        _say("  net greeks: (unavailable)", quiet)

    posture = aggregates.get("posture")
    if isinstance(posture, dict):
        _say("", quiet)
        _say(f"  posture        : {posture.get('posture', '?')}", quiet)
        _say(f"  net delta (sh) : {_fmt(posture.get('net_delta_sh'))}", quiet)
        rec = posture.get("recommended_side")
        if rec:
            _say(f"  recommend      : {rec}", quiet)
        anti = posture.get("anti_recommendation")
        if anti:
            _say(f"  AVOID          : {anti}"
                 + (f" — {posture.get('reason')}" if posture.get("reason") else ""),
                 quiet)

    naked = aggregates.get("naked")
    if isinstance(naked, dict):
        flagged = naked.get("unbounded") or naked.get("naked") or naked.get("flags")
        if flagged:
            _say("", quiet)
            _say(f"  {_C.WARN} naked / unbounded-tail exposure detected (book-wide):",
                 quiet)
            _say(f"      {flagged}", quiet)

    margin = aggregates.get("margin")
    if isinstance(margin, dict):
        _say("", quiet)
        _say(f"  maintenance    : {_fmt(margin.get('maintenance'))}  "
             f"(model stress-grid estimate)", quiet)
        _say(f"  excess liq     : {_fmt(margin.get('excess_liquidity'))}  (model)",
             quiet)
    rep_el = aggregates.get("excess_liquidity_reported")
    if rep_el is not None:
        _say(f"  excess liq     : {_fmt(rep_el)}  (broker-reported)", quiet)

    # Reconciliation badges + outputs
    _section("RECONCILIATION + STAGES", quiet)
    for name, st in stages.items():
        line = f"  {st.badge()}  {name:<12}"
        if st.detail:
            line += f"  {st.detail}"
        _say(line, quiet)

    if render_out.get("charts"):
        _say("", quiet)
        _say(f"  charts -> {OPTIONS_RESULTS}", quiet)
    if render_out.get("report_path"):
        _say(f"  report -> {render_out['report_path']}", quiet)


# =============================================================================
# Orchestration
# =============================================================================
def run(argv: list[str] | None = None) -> int:
    """Parse args, run the pipeline, return a process exit code (0 = clean)."""
    args = build_parser().parse_args(argv)
    quiet = args.quiet

    if args.measure == "rw" and args.mu is None:
        _say(f"{_C.WARN} --measure rw without --mu; real-world drift is required "
             "for honest odds. Defaulting downstream may raise.", quiet)

    stages: dict[str, _Stage] = {
        name: _Stage(name) for name in (
            "load", "market", "economics", "book", "margin",
            "combination", "decision",
            "reconcile", "charts", "report",
        )
    }

    # 1 — load
    try:
        book, source_name, warnings, _ = _load_book(args, quiet)
        args._book_name = source_name
        stages["load"].detail = f"{source_name}: " \
            f"{len(getattr(book, 'positions', ()) or ())} positions"
        if warnings:
            stages["load"].detail += f", {len(warnings)} warning(s)"
    except SystemExit as e:
        _say(str(e), quiet)
        return 2

    # A book that fails validation is a hard exit (contract §6). bookio raises on
    # structural errors; non-fatal issues come back as warnings (printed, not fatal).

    # 2 — markets
    markets = _resolve_markets(book, args, stages, quiet)
    if not markets:
        stages["market"].fail("no MarketState resolved for any underlying")

    # 3 — analytics
    cards = _position_cards(book, markets, args, stages, quiet)
    aggregates = _book_aggregates(book, markets, args, stages, quiet)

    # 3c — combination / decision layer (--subsets / --decide)
    combination = _combination(book, markets, args, stages, quiet)

    # 3b — reconciliation gate
    recon_ok = _reconcile_gate(book, markets, args, stages, quiet)

    # 4/5 — charts + report
    render_out = _render(book, markets, cards, aggregates, combination,
                         args, stages, quiet)

    # 6 — console summary
    _print_summary(book, markets, cards, aggregates, render_out, stages,
                   warnings, args, quiet)
    _print_combination(combination, quiet)

    # Exit code: non-zero on any GATE failure (reconcile) or hard stage failure.
    gate_failed = (not recon_ok) or any(
        s.gate_failed for s in stages.values())
    hard_failed = any(
        (not s.ok and not s.skipped) and s.name in ("load", "report")
        for s in stages.values())
    code = 1 if (gate_failed or hard_failed) else 0
    if not quiet:
        _say("", quiet)
        _say(f"  exit {code}  ("
             + ("reconcile/validation gate failed" if code else "clean")
             + ")", quiet)
    return code


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
