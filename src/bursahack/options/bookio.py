"""Book YAML loader for the options toolkit (§4 schema -> value objects).

This is the *integration glue* between a book YAML on disk and the canonical
value objects in :mod:`bursahack.options.instruments` (the single source of
truth for ``Leg`` / ``MarketState`` / ``Position`` / ``Book`` / ``Account`` /
``AccountProfile``). It does NO pricing, greeks or payoff math -- it only parses
and constructs.

The schema is §4 of the build contract (mirrored in
``configs/options/README.md``):

  asof:      ISO-8601 datetime with tz; expiries must be on/after it.
  defaults:  { r, q, multiplier, currency } -- per-leg fallbacks.
  account:   { tax_status, residence, broker, account_type, cash, netliq,
               excess_liquidity? } -- ``netliq`` is the ONE source for NLV.
  underlyings: [ { symbol, spot, sigma, beta?, hv_series_ref? } ] -- one flat
               ``MarketState`` per name (sigma is the flat-vol fallback).
  scenarios:  optional grid hints (passed through verbatim).
  structures: [ { name?, type?, legs: [ {symbol,right,strike,expiry,qty,
               entry_price, mult?, iv?} ] } ] -- one ``Position`` per entry.
  catalysts:  optional [ { date, label, confirmed? } ].

``right`` is the enum VALUE (C/P/S/X); ``qty`` is signed (+long/-short);
``entry_price`` is the per-share fill MAGNITUDE (the sign of the cash flow comes
from ``qty``).

What :func:`load_book` returns
------------------------------
The CLI (``scripts/run_options_analysis.py``) and the report orchestrator
(``bursahack.options.report``) treat the loaded book as a single duck-typed
object and read these attributes off it (all via ``getattr`` with defaults):

  ``.positions``  tuple[Position, ...]            (also satisfies ``Book``)
  ``.asof``       datetime | None
  ``.account``    Account                          (.profile/.cash/.netliq + .excess_liquidity)
  ``.underlyings`` dict[str, dict]                 ({sym: {spot,sigma,r,q,beta}})
  ``.markets`` / ``.market_by_name`` dict[str, MarketState]
  ``.scenarios`` / ``.catalysts`` / ``.defaults`` pass-through
  ``.warnings``   list[str]                        (non-fatal validation notes)

:class:`LoadedBook` is a frozen :class:`bursahack.options.instruments.Book`
subclass that carries those extra fields, so it *is a* ``Book`` (positions +
asof) while also exposing the resolved markets/account/underlyings the
orchestrator wants -- no second bundle to thread through.

Two string constants -- :data:`TSLA_DEMO_YAML` and :data:`GENERIC_EXAMPLE_YAML`
-- carry the bundled worked examples so ``--demo`` works without a file on disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from bursahack.options.instruments import (
    Account,
    AccountProfile,
    Book,
    Leg,
    MarketState,
    Position,
    Right,
    Style,
)

__all__ = [
    "LoadedBook",
    "load_book",
    "load_book_str",
    "parse_book",
    "TSLA_DEMO_YAML",
    "GENERIC_EXAMPLE_YAML",
]


# ---------------------------------------------------------------------------
# Loaded-book bundle: a Book (positions + asof) that also carries the resolved
# account / markets / underlyings / scenarios / catalysts / warnings the
# orchestrator + CLI read off it. Frozen, like every value object in the pkg.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LoadedBook(Book):
    account: Account | None = None
    underlyings: dict[str, dict[str, Any]] = field(default_factory=dict)
    markets: dict[str, MarketState] = field(default_factory=dict)
    scenarios: dict[str, Any] = field(default_factory=dict)
    catalysts: tuple[dict[str, Any], ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def market_by_name(self) -> dict[str, MarketState]:
        """Alias the CLI/report look for (``getattr(book, 'market_by_name')``)."""
        return self.markets


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------
def load_book(path: str | Path) -> LoadedBook:
    """Load + parse a book YAML at ``path`` into a :class:`LoadedBook`.

    Raises ``FileNotFoundError`` if the path is missing and ``ValueError`` on a
    structurally invalid book (the CLI turns that into a non-zero exit). Soft
    issues (e.g. an expiry before ``asof``) come back as ``.warnings``.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_book(text)


def load_book_str(yaml_text: str) -> LoadedBook:
    """Parse a book from a YAML *string* (used by ``--demo`` with the bundled
    constants, and by the CLI's ``_load_from_yaml_string`` fast path)."""
    return parse_book(yaml_text)


def parse_book(yaml_text: str) -> LoadedBook:
    """Core parser: YAML text -> :class:`LoadedBook`. Pure (no I/O, no network)."""
    import yaml  # lazy; PyYAML is a core dep

    raw = yaml.safe_load(yaml_text) or {}
    if not isinstance(raw, dict):
        raise ValueError("Book YAML must be a mapping at the top level.")

    warnings: list[str] = []

    asof = _parse_dt(raw.get("asof"))
    defaults = dict(raw.get("defaults") or {})
    def_r = _as_float(defaults.get("r"), 0.045)
    def_q = _as_float(defaults.get("q"), 0.0)
    def_mult = int(defaults.get("multiplier", 100) or 100)

    account = _parse_account(raw.get("account"))
    underlyings = _parse_underlyings(raw.get("underlyings"))
    markets = _build_markets(underlyings, def_r, def_q, asof)

    asof_date = asof.date() if isinstance(asof, datetime) else (
        asof if isinstance(asof, date) else None
    )
    positions = _parse_structures(
        raw.get("structures"), def_mult, asof_date, warnings
    )

    scenarios = dict(raw.get("scenarios") or {})
    catalysts = _parse_catalysts(raw.get("catalysts"))

    if not positions:
        raise ValueError("Book has no positions (empty/missing 'structures').")

    return LoadedBook(
        positions=tuple(positions),
        asof=asof,
        account=account,
        underlyings=underlyings,
        markets=markets,
        scenarios=scenarios,
        catalysts=catalysts,
        defaults=defaults,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Section parsers (all pure)
# ---------------------------------------------------------------------------
def _parse_account(node: Any) -> Account | None:
    if not isinstance(node, dict):
        return None
    profile = AccountProfile(
        tax_status=str(node.get("tax_status", "nra")),
        residence=str(node.get("residence", "MY")),
        broker=str(node.get("broker", "IBKR")),
        account_type=str(node.get("account_type", "portfolio_margin")),
    )
    cash = _as_float(node.get("cash"), 0.0)
    netliq = _as_float(node.get("netliq"), 0.0)
    account = Account(profile=profile, cash=cash, netliq=netliq)
    # ExcessLiquidity is broker-reported context (not on the frozen Account).
    # Attach it so the CLI's _account_excess_liq(book.account) can read it.
    excess = node.get("excess_liquidity")
    if excess is not None:
        object.__setattr__(account, "excess_liquidity", _as_float(excess, 0.0))
    return account


def _parse_underlyings(node: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(node, (list, tuple)):
        return out
    for row in node:
        if not isinstance(row, dict):
            continue
        sym = row.get("symbol") or row.get("underlying")
        if not sym:
            continue
        meta = dict(row)
        meta.setdefault("symbol", str(sym))
        out[str(sym)] = meta
    return out


def _build_markets(
    underlyings: dict[str, dict[str, Any]],
    def_r: float,
    def_q: float,
    asof: datetime | None,
) -> dict[str, MarketState]:
    markets: dict[str, MarketState] = {}
    for sym, meta in underlyings.items():
        spot = _as_float(meta.get("spot"), 0.0)
        if spot <= 0:
            # MarketState requires spot > 0; skip rather than crash the load.
            continue
        markets[sym] = MarketState(
            spot=spot,
            r=_as_float(meta.get("r"), def_r),
            q=_as_float(meta.get("q"), def_q),
            sigma=_as_float(meta.get("sigma"), 0.0),
            asof=asof,
        )
    return markets


def _parse_structures(
    node: Any,
    def_mult: int,
    asof_date: date | None,
    warnings: list[str],
) -> list[Position]:
    positions: list[Position] = []
    if not isinstance(node, (list, tuple)):
        return positions
    for idx, struct in enumerate(node):
        if not isinstance(struct, dict):
            continue
        name = str(struct.get("name", "") or "")
        raw_legs = struct.get("legs") or []
        legs: list[Leg] = []
        underlying = ""
        for leg_row in raw_legs:
            if not isinstance(leg_row, dict):
                continue
            leg = _parse_leg(leg_row, def_mult, asof_date, name or f"#{idx}", warnings)
            legs.append(leg)
            if not underlying and leg.underlying:
                underlying = leg.underlying
        if not legs:
            warnings.append(f"structure '{name or idx}' has no legs; skipped")
            continue
        positions.append(Position(underlying=underlying, legs=tuple(legs), name=name))
    return positions


def _parse_leg(
    row: dict[str, Any],
    def_mult: int,
    asof_date: date | None,
    struct_label: str,
    warnings: list[str],
) -> Leg:
    right = Right(str(row["right"]))
    expiry = _parse_date(row.get("expiry"))
    if (
        expiry is not None
        and asof_date is not None
        and expiry < asof_date
    ):
        warnings.append(
            f"{struct_label}: leg expiry {expiry} is before asof {asof_date}"
        )
    mult = int(row.get("mult", row.get("multiplier", def_mult)) or def_mult)
    style_raw = row.get("style")
    style = Style(str(style_raw)) if style_raw else Style.EUROPEAN
    iv = row.get("iv")
    return Leg(
        right=right,
        strike=_as_float(row.get("strike"), 0.0),
        expiry=expiry,
        qty=_as_float(row.get("qty"), 0.0),
        mult=mult,
        entry_price=_as_float(row.get("entry_price", row.get("entry")), 0.0),
        style=style,
        iv=(float(iv) if iv is not None else None),
        underlying=str(row.get("symbol", row.get("underlying", "")) or ""),
    )


def _parse_catalysts(node: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(node, (list, tuple)):
        return ()
    return tuple(dict(c) for c in node if isinstance(c, dict))


# ---------------------------------------------------------------------------
# Small coercers
# ---------------------------------------------------------------------------
def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    # tolerate a trailing 'Z' (UTC) for fromisoformat on older interpreters
    norm = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(norm)
    except ValueError:
        try:
            return datetime.fromisoformat(norm[:10])
        except ValueError:
            return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Bundled worked-example books (so ``--demo`` needs no file on disk).
# ---------------------------------------------------------------------------
# The canonical 360/460 x8 bull-call worked example (the §7 golden), pinned to
# market state MS_A (S=389.80, sigma=0.46).
TSLA_DEMO_YAML = """\
asof: 2026-06-18T16:00:00-04:00

defaults:
  r: 0.045
  q: 0.0
  multiplier: 100
  currency: USD

account:
  tax_status: nra
  residence: MY
  broker: IBKR
  account_type: portfolio_margin
  cash: 0
  netliq: 525898
  excess_liquidity: 84000

underlyings:
  - symbol: TSLA
    spot: 389.80
    sigma: 0.46
    beta: 1.8
    hv_series_ref: null

scenarios:
  spots_pct: [-0.20, -0.10, -0.05, 0, 0.05, 0.10, 0.20]
  iv_shifts: [-10, -5, 0, 5, 10]
  days: [0, 7, 30]

structures:
  - name: TSLA bull call 360/460 Jun27 x8
    type: bull_call_spread
    legs:
      - { symbol: TSLA, right: C, strike: 360, expiry: 2027-06-18, qty:  8, entry_price: 91.96 }
      - { symbol: TSLA, right: C, strike: 460, expiry: 2027-06-18, qty: -8, entry_price: 53.00 }

catalysts:
  - { date: 2026-07-23, label: TSLA Q2 earnings, confirmed: false }
"""

# Generic single-name iron condor on placeholder ACME (underlying-agnostic proof).
GENERIC_EXAMPLE_YAML = """\
asof: 2026-06-18T16:00:00-04:00

defaults:
  r: 0.045
  q: 0.0
  multiplier: 100
  currency: USD

account:
  tax_status: nra
  residence: MY
  broker: IBKR
  account_type: reg_t_margin
  cash: 25000
  netliq: 25000

underlyings:
  - symbol: ACME
    spot: 100.00
    sigma: 0.30
    beta: 1.0
    hv_series_ref: null

scenarios:
  spots_pct: [-0.20, -0.10, -0.05, 0, 0.05, 0.10, 0.20]
  iv_shifts: [-10, -5, 0, 5, 10]
  days: [0, 7, 30]

structures:
  - name: ACME iron condor 85/90/110/115
    type: iron_condor
    legs:
      - { symbol: ACME, right: P, strike:  85, expiry: 2026-09-18, qty:   1, entry_price: 0.55 }
      - { symbol: ACME, right: P, strike:  90, expiry: 2026-09-18, qty:  -1, entry_price: 1.20 }
      - { symbol: ACME, right: C, strike: 110, expiry: 2026-09-18, qty:  -1, entry_price: 1.35 }
      - { symbol: ACME, right: C, strike: 115, expiry: 2026-09-18, qty:   1, entry_price: 0.60 }

catalysts: []
"""
