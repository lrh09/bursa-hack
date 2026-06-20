"""Market-data boundary: the canonical option-chain façade + pure date/health helpers.

This module is the *named* market-data entry point the CLI and downstream
consumers import (``scripts/run_options_analysis.py`` does ``_imp(
"bursahack.options.marketdata")`` and calls ``md.fetch_chain(sym, None)``). It is
a thin façade over the live loader in :mod:`bursahack.options.chain` — all the
wire-touching, normalization and microstructure logic lives there; here we only

  1. re-export the chain contract (``OptionQuote`` / ``ChainSnapshot`` /
     ``CatalystDates`` value objects, ``fetch_chain`` / ``list_expiries`` /
     ``fetch_catalysts`` / ``liquidity_score``, plus ``ChainDataUnavailable``),
  2. adapt ``fetch_chain`` so an OMITTED / ``None`` expiry auto-selects the
     nearest available expiry (the CLI passes ``None`` on ``--fetch SYM``), and
  3. add the *pure, offline-safe* helpers the consumers need but that do not
     belong on the wire-loader: ``time_to_expiry`` (calendar-years to an expiry),
     a quote-level ``pricing_view`` and a snapshot-level ``chain_health``.

Design law L4: pure analytic cores never import this module; yfinance stays a
lazy, optional ``research`` extra inside :mod:`chain`. Importing ``marketdata``
never drags in the network dependency.

Sign / unit conventions follow :mod:`bursahack.options.types`: time in YEARS
(``time_to_expiry`` converts calendar dates with a 365-day convention), per-leg
multiplier is 100 by default and is passed through, never hardcoded into math.

References:
  - bursahack.options.chain  (the live yfinance loader this façade re-exports)
  - Build contract §marketdata (OptionQuote / ChainSnapshot / pricing_view /
    liquidity_score / time_to_expiry contract).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

# Re-export the entire chain contract so callers can import everything from the
# single market-data namespace. chain.py is the canonical (research-extra) loader
# whose value objects are field-compatible with the marketdata contract.
from bursahack.options.chain import (  # noqa: F401  (re-export surface)
    DEFAULT_LIQUIDITY,
    CatalystDates,
    ChainDataUnavailable,
    ChainSnapshot,
    LiquidityConfig,
    OptionQuote,
    fetch_catalysts,
    liquidity_score,
)
from bursahack.options.chain import fetch_chain as _chain_fetch_chain
from bursahack.options.chain import list_expiries as list_expiries

__all__ = [
    # value objects / config
    "OptionQuote",
    "ChainSnapshot",
    "CatalystDates",
    "LiquidityConfig",
    "DEFAULT_LIQUIDITY",
    # errors
    "ChainDataUnavailable",
    # live API (re-exported / adapted)
    "fetch_chain",
    "list_expiries",
    "fetch_catalysts",
    "liquidity_score",
    # pure helpers added here
    "time_to_expiry",
    "pricing_view",
    "chain_health",
]


# ---------------------------------------------------------------------------
# Pure date helper (offline-safe; the precise T-in-years the pipeline wants)
# ---------------------------------------------------------------------------
def time_to_expiry(expiry: date | datetime | str, asof: date | datetime | None = None) -> float:
    """Calendar years from ``asof`` (default now, UTC) to ``expiry`` (365-day basis).

    Pure / offline. Accepts ``expiry`` as a ``date``, a ``datetime`` (its date is
    used), or an ISO ``'YYYY-MM-DD'`` string. ``asof`` may be a ``date`` /
    ``datetime`` / ``None`` (today, UTC). A past expiry clamps to ``0.0`` — time
    never runs backwards for pricing. Day-count is calendar days / 365.0 to match
    the package's L5 year convention used by ``strategy._years_to`` and the
    scenario engine.
    """
    exp_d = _coerce_date(expiry)
    if exp_d is None:
        raise ValueError(f"time_to_expiry: could not parse expiry {expiry!r}")
    if asof is None:
        base = datetime.now(timezone.utc).date()
    else:
        base = _coerce_date(asof) or datetime.now(timezone.utc).date()
    days = (exp_d - base).days
    return max(days, 0) / 365.0


def _coerce_date(value: date | datetime | str | None) -> date | None:
    """Coerce a date / datetime / ISO-string to a calendar ``date`` (else None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Live-fetch adapter — tolerate an omitted / None expiry (CLI passes None)
# ---------------------------------------------------------------------------
def fetch_chain(symbol: str, expiry: str | date | None = None, *,
                with_catalysts: bool = True) -> ChainSnapshot:
    """Fetch & normalize the live option chain for ``symbol``.

    Façade over :func:`bursahack.options.chain.fetch_chain` that additionally
    accepts an OMITTED / ``None`` ``expiry``: in that case the nearest available
    expiry (the soonest future one, else the first listed) is selected via
    :func:`list_expiries`. This is what the CLI relies on when invoked as
    ``--fetch SYM`` (it calls ``md.fetch_chain(sym, None)``).

    Raises :class:`ChainDataUnavailable` (from the underlying loader) when the
    network / optional ``yfinance`` dependency is missing, the symbol is unknown,
    or the expiry has no chain — never returns silent NaNs.
    """
    if expiry is None:
        expiry = _nearest_expiry(symbol)
    return _chain_fetch_chain(symbol, expiry, with_catalysts=with_catalysts)


def _nearest_expiry(symbol: str) -> date:
    """Pick the soonest future expiry for ``symbol`` (else the first listed)."""
    expiries = list_expiries(symbol)  # may raise ChainDataUnavailable
    if not expiries:
        raise ChainDataUnavailable(
            f"No option expiries available for {symbol!r}; cannot auto-select one."
        )
    today = datetime.now(timezone.utc).date()
    future = sorted(e for e in expiries if e >= today)
    return future[0] if future else sorted(expiries)[0]


# ---------------------------------------------------------------------------
# Pure microstructure / health views (no I/O) — operate on already-fetched data
# ---------------------------------------------------------------------------
def pricing_view(quote: OptionQuote, mult: int = 100) -> dict[str, float | str]:
    """Microstructure snapshot for one quote (mid / spread / cross-cost / source).

    Thin pure pass-through to :meth:`OptionQuote.pricing_view` so callers that hold
    a ``marketdata`` reference do not need to reach into the chain module. ``mult``
    is the per-leg multiplier used to dollar-ize the half-spread cross cost.
    """
    return quote.pricing_view(mult=mult)


def chain_health(snapshot: ChainSnapshot, cfg: LiquidityConfig = DEFAULT_LIQUIDITY) -> dict:
    """Coarse tradeability summary for a whole :class:`ChainSnapshot` (pure).

    Aggregates the per-quote liquidity score over the snapshot's rows into a
    median / mean score, a count of ``liquid``-bucket strikes, the ATM call & put
    scores, and a single ``tradeable`` verdict (median score >= 40 = "moderate"
    or better). Offline / deterministic — no re-fetch.

    Returns::

        {
          'n_rows', 'n_calls', 'n_puts',
          'median_score', 'mean_score',
          'n_liquid', 'n_thin_or_illiquid',
          'atm_call_score', 'atm_put_score',
          'tradeable',
        }
    """
    rows = snapshot.rows
    n = len(rows)
    if n == 0:
        return {
            "n_rows": 0, "n_calls": 0, "n_puts": 0,
            "median_score": 0.0, "mean_score": 0.0,
            "n_liquid": 0, "n_thin_or_illiquid": 0,
            "atm_call_score": None, "atm_put_score": None,
            "tradeable": False,
        }

    scores = [float(liquidity_score(q, cfg)["score"]) for q in rows]
    scores_sorted = sorted(scores)
    mid = n // 2
    median = (scores_sorted[mid] if n % 2 == 1
              else 0.5 * (scores_sorted[mid - 1] + scores_sorted[mid]))
    mean = sum(scores) / n
    n_liquid = sum(1 for s in scores if s >= 70.0)
    n_thin = sum(1 for s in scores if s < 40.0)

    from bursahack.options.types import Right  # local: avoid eager core->md edge

    atm_call = snapshot.atm(Right.CALL)
    atm_put = snapshot.atm(Right.PUT)
    atm_call_score = (float(liquidity_score(atm_call, cfg)["score"])
                      if atm_call is not None else None)
    atm_put_score = (float(liquidity_score(atm_put, cfg)["score"])
                     if atm_put is not None else None)

    return {
        "n_rows": n,
        "n_calls": len(snapshot.calls()),
        "n_puts": len(snapshot.puts()),
        "median_score": median,
        "mean_score": mean,
        "n_liquid": n_liquid,
        "n_thin_or_illiquid": n_thin,
        "atm_call_score": atm_call_score,
        "atm_put_score": atm_put_score,
        "tradeable": median >= 40.0,
    }
