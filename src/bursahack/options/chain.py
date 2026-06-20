"""Live option-chain ingestion via yfinance, normalized to contract dataclasses.

This is the *research* I/O boundary for the options toolkit: the only place that
reaches the wire for an option chain. It is **offline-safe** and **lazy** —
``yfinance`` is imported inside functions (never at module top), and every public
entry point raises a clear, typed error when the network or the optional
dependency is unavailable. Pure analytics (``core``/``bsm``/``prob`` …) MUST NOT
import this module (design law L4).

What it produces
----------------
- ``OptionQuote``  : one normalized option row (strike, right, bid/ask/last,
  volume, OI, quoted IV, last-trade time, contract symbol).
- ``ChainSnapshot``: an immutable point-in-time slice for one underlying+expiry,
  carrying the spot and the (call+put) rows. Field-compatible with the
  ``marketdata.ChainSnapshot`` contract (§marketdata) so downstream lenses
  compose regardless of which loader produced the snapshot.
- ``CatalystDates`` : next earnings date + upcoming ex-dividend / dividend yield.

Microstructure helpers
-----------------------
For a quote with bid ``b`` and ask ``a`` (mid ``m = (a+b)/2``)::

    spread        = a - b
    rel_spread    = (a - b) / m                     (0 when m <= 0)
    mid           = m                               (fallback to last when crossed/empty)
    cross_cost    = (a - b) / 2 * mult              ($ to cross half the spread)

Liquidity score (0..100, higher = more liquid) blends three normalized,
saturating components — tighter relative spread, deeper open interest, and
higher session volume::

    spread_pts = 100 * (1 - clip(rel_spread / SPREAD_CEIL, 0, 1))
    oi_pts     = 100 * min(1, log1p(open_interest) / log1p(OI_SAT))
    vol_pts    = 100 * min(1, log1p(volume)        / log1p(VOL_SAT))
    score      = w_s*spread_pts + w_oi*oi_pts + w_v*vol_pts

Sign / unit conventions follow ``types``: time in years elsewhere; this module
only deals in quotes and calendar dates. Multiplier is per-leg (100 default),
never hardcoded in math — callers pass it through to the ``$``-denominated
microstructure fields.

References:
  - yfinance Ticker.option_chain / .options / .get_earnings_dates / .dividends
  - bursahack.options.types  (Right enum; OptionQuote/ChainSnapshot field contract)
  - Build contract §marketdata (OptionQuote, ChainSnapshot, pricing_view,
    liquidity_score) — this module is the live (research-extra) loader for them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from bursahack.options.types import Right

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed at runtime
    import pandas as pd


# ---------------------------------------------------------------------------
# Errors (offline-safe boundary)
# ---------------------------------------------------------------------------
class ChainDataUnavailable(RuntimeError):
    """Raised when an option chain cannot be obtained.

    Covers three distinct, *clearly-messaged* failure modes:
      - ``yfinance`` (the optional ``research`` extra) is not installed,
      - the network is unreachable / the provider returned nothing,
      - the requested symbol / expiry does not exist.

    The message always states which case occurred and how to remedy it, so an
    offline run fails loudly and informatively rather than emitting NaNs.
    """


# ---------------------------------------------------------------------------
# Tunables for the liquidity score (frozen so they are part of the contract)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LiquidityConfig:
    """Saturation points & weights for ``liquidity_score`` (0..100)."""

    spread_ceiling: float = 0.25   # rel_spread at/above which spread_pts -> 0 (25%)
    oi_saturation: float = 5_000.0  # open interest giving ~full oi_pts
    vol_saturation: float = 2_000.0  # session volume giving ~full vol_pts
    w_spread: float = 0.5
    w_oi: float = 0.3
    w_volume: float = 0.2


DEFAULT_LIQUIDITY = LiquidityConfig()


# ---------------------------------------------------------------------------
# Normalized value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OptionQuote:
    """One normalized option-chain row.

    Field set is a superset-compatible match for ``marketdata.OptionQuote`` so a
    snapshot from this loader drops into the same downstream code. NaNs from the
    provider are coerced to ``0.0`` / ``0`` here (the normalization point), never
    propagated.
    """

    strike: float
    right: Right
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    iv_quote: float                      # provider's quoted IV (decimal, e.g. 0.46)
    last_trade: datetime | None
    contract_symbol: str

    # ---- microstructure (pure, no I/O) -----------------------------------
    @property
    def mid(self) -> float:
        """(bid+ask)/2 when both sides present & not crossed; else falls back to
        ``last`` (then to the single live side, then 0.0)."""
        if self.bid > 0.0 and self.ask > 0.0 and self.ask >= self.bid:
            return 0.5 * (self.bid + self.ask)
        if self.last > 0.0:
            return self.last
        if self.ask > 0.0:
            return self.ask
        if self.bid > 0.0:
            return self.bid
        return 0.0

    @property
    def spread(self) -> float:
        """Absolute bid/ask spread (0.0 if a side is missing or crossed)."""
        if self.bid > 0.0 and self.ask > 0.0 and self.ask >= self.bid:
            return self.ask - self.bid
        return 0.0

    @property
    def rel_spread(self) -> float:
        """Spread relative to mid (0.0 when mid <= 0)."""
        m = self.mid
        if m <= 0.0:
            return 0.0
        return self.spread / m

    def pricing_view(self, mult: int = 100) -> dict[str, float | str]:
        """Microstructure snapshot for this quote (mirrors
        ``marketdata.pricing_view``). ``cross_cost`` is the $ to pay half the
        spread for one contract at the given multiplier."""
        m = self.mid
        if self.bid > 0.0 and self.ask > 0.0 and self.ask >= self.bid:
            source = "mid"
        elif self.last > 0.0:
            source = "last"
        else:
            source = "stale"
        return {
            "mid": m,
            "spread": self.spread,
            "rel_spread": self.rel_spread,
            "cross_cost": 0.5 * self.spread * mult,
            "price_source": source,
        }

    def liquidity_score(self, cfg: LiquidityConfig = DEFAULT_LIQUIDITY) -> dict[str, float | str]:
        """0..100 liquidity score + components + bucket. Pure (no I/O)."""
        return liquidity_score(self, cfg)


@dataclass(frozen=True)
class CatalystDates:
    """Next earnings + dividend context for an underlying (calendar dates)."""

    symbol: str
    next_earnings: date | None
    next_ex_dividend: date | None
    dividend_amount: float       # most-recent / upcoming cash dividend per share
    dividend_yield: float        # trailing-12m yield (decimal; 0.0 if none/unknown)


@dataclass(frozen=True)
class ChainSnapshot:
    """Immutable point-in-time option chain for one underlying + expiry.

    Field-compatible with ``marketdata.ChainSnapshot``: ``underlying``,
    ``asof_utc``, ``spot``, ``expiry``, ``rows``. ``rows`` holds calls and puts
    (sorted by (right, strike)). Convenience accessors split / filter them
    without re-fetching.
    """

    underlying: str
    asof_utc: datetime
    spot: float
    expiry: date
    rows: tuple[OptionQuote, ...]
    catalysts: CatalystDates | None = None
    source: str = "yfinance"

    # ---- pure accessors --------------------------------------------------
    def calls(self) -> tuple[OptionQuote, ...]:
        return tuple(q for q in self.rows if q.right is Right.CALL)

    def puts(self) -> tuple[OptionQuote, ...]:
        return tuple(q for q in self.rows if q.right is Right.PUT)

    def strikes(self) -> tuple[float, ...]:
        """Sorted unique strikes across both sides."""
        return tuple(sorted({q.strike for q in self.rows}))

    def quote(self, strike: float, right: Right, tol: float = 1e-6) -> OptionQuote | None:
        """Exact-strike lookup (within ``tol``) for a given right; None if absent."""
        for q in self.rows:
            if q.right is right and abs(q.strike - strike) <= tol:
                return q
        return None

    def atm(self, right: Right) -> OptionQuote | None:
        """Quote whose strike is nearest the spot for the given right."""
        side = [q for q in self.rows if q.right is right]
        if not side:
            return None
        return min(side, key=lambda q: abs(q.strike - self.spot))


# ---------------------------------------------------------------------------
# Pure microstructure / liquidity (no I/O) — safe for any caller
# ---------------------------------------------------------------------------
def liquidity_score(
    quote: OptionQuote, cfg: LiquidityConfig = DEFAULT_LIQUIDITY
) -> dict[str, float | str]:
    """Blend rel-spread, open interest and volume into a 0..100 liquidity score.

    Each component saturates so a single deep input cannot dominate. Returns the
    score, its three components, and a coarse human bucket. Pure function — no
    network, deterministic.
    """
    rel = quote.rel_spread
    if cfg.spread_ceiling <= 0.0:
        spread_pts = 0.0
    else:
        spread_pts = 100.0 * (1.0 - _clip(rel / cfg.spread_ceiling, 0.0, 1.0))

    oi_pts = 100.0 * _log_saturate(quote.open_interest, cfg.oi_saturation)
    vol_pts = 100.0 * _log_saturate(quote.volume, cfg.vol_saturation)

    score = cfg.w_spread * spread_pts + cfg.w_oi * oi_pts + cfg.w_volume * vol_pts
    score = _clip(score, 0.0, 100.0)

    if score >= 70.0:
        bucket = "liquid"
    elif score >= 40.0:
        bucket = "moderate"
    elif score >= 15.0:
        bucket = "thin"
    else:
        bucket = "illiquid"

    return {
        "score": score,
        "spread_pts": spread_pts,
        "oi_pts": oi_pts,
        "vol_pts": vol_pts,
        "rel_spread": rel,
        "bucket": bucket,
    }


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _log_saturate(value: float, sat: float) -> float:
    """``log1p(value) / log1p(sat)`` clipped to [0,1]; 0.0 when sat<=0."""
    if sat <= 0.0 or value <= 0.0:
        return 0.0
    return _clip(math.log1p(value) / math.log1p(sat), 0.0, 1.0)


# ---------------------------------------------------------------------------
# Lazy yfinance import (the single wire-touching helper)
# ---------------------------------------------------------------------------
def _lazy_yfinance() -> Any:
    """Import yfinance lazily; raise a clear, actionable error if missing.

    Never imported at module top (design law L4: pure core must import this
    module without dragging in the optional research dependency).
    """
    try:
        import yfinance as yf  # noqa: PLC0415 - intentional lazy import
    except Exception as exc:  # ImportError or transitive import failures
        raise ChainDataUnavailable(
            "yfinance is not installed. It is an OPTIONAL dependency in the "
            "'research' extra. Install with:  uv pip install 'bursahack[research]'  "
            "(or  pip install yfinance ). Live option-chain fetch is unavailable "
            "until then; offline analytics still work."
        ) from exc
    return yf


# ---------------------------------------------------------------------------
# Normalization (provider DataFrame -> contract dataclasses)
# ---------------------------------------------------------------------------
def _f(value: Any, default: float = 0.0) -> float:
    """Coerce a possibly-NaN/None provider cell to a finite float."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _i(value: Any, default: int = 0) -> int:
    """Coerce a possibly-NaN/None provider cell to an int (0 on failure)."""
    f = _f(value, float(default))
    try:
        return int(f)
    except (TypeError, ValueError, OverflowError):
        return default


def _last_trade(value: Any) -> datetime | None:
    """Coerce a pandas Timestamp / datetime to a tz-aware datetime, else None."""
    if value is None:
        return None
    # pandas Timestamp has .to_pydatetime(); guard via duck-typing (no top import).
    to_py = getattr(value, "to_pydatetime", None)
    dt: datetime | None
    if callable(to_py):
        try:
            dt = to_py()
        except Exception:
            dt = None
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = None
    if dt is None:
        return None
    # NaT (pandas missing) round-trips to a datetime that compares unequal to self.
    try:
        if dt != dt:  # NaN/NaT sentinel
            return None
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rows_from_frame(frame: "pd.DataFrame", right: Right) -> list[OptionQuote]:
    """Normalize one side (calls or puts) of a yfinance option_chain frame.

    yfinance columns of interest: ``strike, bid, ask, lastPrice, volume,
    openInterest, impliedVolatility, lastTradeDate, contractSymbol``. Missing
    columns degrade gracefully to defaults.
    """
    quotes: list[OptionQuote] = []
    if frame is None or getattr(frame, "empty", True):
        return quotes
    cols = set(frame.columns)
    for _, row in frame.iterrows():
        get = row.get if hasattr(row, "get") else (lambda k, d=None: row[k] if k in cols else d)
        quotes.append(
            OptionQuote(
                strike=_f(get("strike")),
                right=right,
                bid=_f(get("bid")),
                ask=_f(get("ask")),
                last=_f(get("lastPrice")),
                volume=_i(get("volume")),
                open_interest=_i(get("openInterest")),
                iv_quote=_f(get("impliedVolatility")),
                last_trade=_last_trade(get("lastTradeDate")),
                contract_symbol=str(get("contractSymbol") or ""),
            )
        )
    return quotes


def _parse_expiry(expiry: str | date) -> tuple[str, date]:
    """Return (yfinance_str 'YYYY-MM-DD', date) for an expiry given either way."""
    if isinstance(expiry, date) and not isinstance(expiry, datetime):
        return expiry.isoformat(), expiry
    if isinstance(expiry, datetime):
        d = expiry.date()
        return d.isoformat(), d
    s = str(expiry)
    try:
        d = date.fromisoformat(s[:10])
    except ValueError as exc:
        raise ChainDataUnavailable(
            f"Could not parse expiry {expiry!r}; expected a date or 'YYYY-MM-DD'."
        ) from exc
    return d.isoformat(), d


# ---------------------------------------------------------------------------
# Public live-fetch API (the only wire-touching entry points)
# ---------------------------------------------------------------------------
def list_expiries(symbol: str) -> list[date]:
    """Available option expiries for ``symbol`` (live). Offline-safe errors."""
    yf = _lazy_yfinance()
    try:
        tk = yf.Ticker(symbol)
        raw = list(tk.options or [])
    except ChainDataUnavailable:
        raise
    except Exception as exc:
        raise ChainDataUnavailable(
            f"Could not reach the provider for {symbol!r} expiries "
            f"(network down or symbol invalid): {exc}"
        ) from exc
    if not raw:
        raise ChainDataUnavailable(
            f"No option expiries returned for {symbol!r} — it may not be "
            "optionable, may be delisted, or the provider is offline."
        )
    out: list[date] = []
    for s in raw:
        try:
            out.append(date.fromisoformat(str(s)[:10]))
        except ValueError:
            continue
    return out


def _spot_from_ticker(tk: Any) -> float:
    """Best-effort live spot from a yfinance Ticker (fast_info -> info -> hist)."""
    # fast_info is cheapest and most reliable on recent yfinance.
    fi = getattr(tk, "fast_info", None)
    if fi is not None:
        for key in ("last_price", "lastPrice", "regularMarketPrice"):
            try:
                val = fi[key] if not hasattr(fi, key) else getattr(fi, key)
            except Exception:
                val = None
            s = _f(val)
            if s > 0.0:
                return s
    info = None
    try:
        info = tk.info
    except Exception:
        info = None
    if isinstance(info, dict):
        for key in ("regularMarketPrice", "currentPrice", "previousClose"):
            s = _f(info.get(key))
            if s > 0.0:
                return s
    try:
        hist = tk.history(period="1d")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return _f(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def fetch_chain(symbol: str, expiry: str | date, *, with_catalysts: bool = True) -> ChainSnapshot:
    """Fetch & normalize the live option chain for ``symbol`` at ``expiry``.

    Lazy-imports yfinance; raises :class:`ChainDataUnavailable` with a clear
    message if the dependency or network is missing, the symbol is unknown, or
    the expiry has no chain. Returns a :class:`ChainSnapshot` of normalized
    calls+puts (sorted by (right, strike)), the live spot, and — unless
    ``with_catalysts=False`` — the next earnings / ex-dividend context.
    """
    yf = _lazy_yfinance()
    exp_str, exp_date = _parse_expiry(expiry)

    try:
        tk = yf.Ticker(symbol)
        oc = tk.option_chain(exp_str)
    except ChainDataUnavailable:
        raise
    except Exception as exc:
        raise ChainDataUnavailable(
            f"Could not fetch the {symbol!r} option chain for {exp_str} "
            f"(network down, symbol invalid, or expiry has no chain): {exc}"
        ) from exc

    calls_frame = getattr(oc, "calls", None)
    puts_frame = getattr(oc, "puts", None)
    rows = _rows_from_frame(calls_frame, Right.CALL) + _rows_from_frame(puts_frame, Right.PUT)
    if not rows:
        raise ChainDataUnavailable(
            f"The {symbol!r} chain for {exp_str} came back empty — the provider "
            "may be rate-limiting or the expiry is invalid."
        )
    rows.sort(key=lambda q: (q.right.value, q.strike))

    spot = _spot_from_ticker(tk)

    catalysts: CatalystDates | None = None
    if with_catalysts:
        try:
            catalysts = fetch_catalysts(symbol, _ticker=tk)
        except ChainDataUnavailable:
            catalysts = None  # catalysts are best-effort; never sink the chain fetch

    return ChainSnapshot(
        underlying=symbol.upper(),
        asof_utc=datetime.now(timezone.utc),
        spot=spot,
        expiry=exp_date,
        rows=tuple(rows),
        catalysts=catalysts,
        source="yfinance",
    )


def fetch_catalysts(symbol: str, *, _ticker: Any | None = None) -> CatalystDates:
    """Next earnings date + upcoming ex-dividend / dividend yield for ``symbol``.

    Best-effort: any field the provider cannot supply comes back ``None`` / 0.0
    rather than raising — only a total provider/network failure raises
    :class:`ChainDataUnavailable`. The optional ``_ticker`` lets ``fetch_chain``
    reuse an already-constructed Ticker (avoids a second handshake).
    """
    if _ticker is not None:
        tk = _ticker
    else:
        yf = _lazy_yfinance()
        try:
            tk = yf.Ticker(symbol)
        except Exception as exc:
            raise ChainDataUnavailable(
                f"Could not reach the provider for {symbol!r} catalysts: {exc}"
            ) from exc

    next_earnings = _next_earnings(tk)
    next_ex_div, div_amt = _next_ex_dividend(tk)
    div_yield = _dividend_yield(tk)

    return CatalystDates(
        symbol=symbol.upper(),
        next_earnings=next_earnings,
        next_ex_dividend=next_ex_div,
        dividend_amount=div_amt,
        dividend_yield=div_yield,
    )


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _to_date(value: Any) -> date | None:
    """Coerce a Timestamp/datetime/str/date to a calendar date, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    to_py = getattr(value, "to_pydatetime", None)
    if callable(to_py):
        try:
            dt = to_py()
            return dt.date() if isinstance(dt, datetime) else None
        except Exception:
            return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _next_earnings(tk: Any) -> date | None:
    """Soonest earnings date >= today from get_earnings_dates / calendar."""
    today = _today_utc()
    candidates: list[date] = []

    getter = getattr(tk, "get_earnings_dates", None)
    if callable(getter):
        try:
            df = getter(limit=16)
        except Exception:
            df = None
        if df is not None and not getattr(df, "empty", True):
            try:
                for idx in df.index:
                    d = _to_date(idx)
                    if d is not None:
                        candidates.append(d)
            except Exception:
                pass

    if not candidates:
        try:
            cal = tk.calendar
        except Exception:
            cal = None
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date") or cal.get("EarningsDate")
        elif cal is not None and hasattr(cal, "loc"):
            try:
                ed = cal.loc["Earnings Date"]
            except Exception:
                ed = None
        if ed is not None:
            seq = ed if isinstance(ed, (list, tuple)) else [ed]
            for v in seq:
                d = _to_date(v)
                if d is not None:
                    candidates.append(d)

    future = sorted(d for d in candidates if d >= today)
    return future[0] if future else None


def _next_ex_dividend(tk: Any) -> tuple[date | None, float]:
    """Upcoming ex-dividend date (from .calendar) + most-recent cash amount.

    yfinance does not expose forward ex-div via the dividend *series*; the
    forward ex-div date lives in ``.calendar``. The amount falls back to the
    last paid dividend from the ``.dividends`` series.
    """
    ex_date: date | None = None
    try:
        cal = tk.calendar
    except Exception:
        cal = None
    if isinstance(cal, dict):
        ex_date = _to_date(cal.get("Ex-Dividend Date") or cal.get("ExDividendDate"))
    elif cal is not None and hasattr(cal, "loc"):
        try:
            ex_date = _to_date(cal.loc["Ex-Dividend Date"])
        except Exception:
            ex_date = None

    amount = 0.0
    try:
        divs = tk.dividends
    except Exception:
        divs = None
    if divs is not None and not getattr(divs, "empty", True):
        try:
            amount = _f(divs.iloc[-1])
        except Exception:
            amount = 0.0

    return ex_date, amount


def _dividend_yield(tk: Any) -> float:
    """Trailing dividend yield (decimal) from fast_info/info; 0.0 if none."""
    fi = getattr(tk, "fast_info", None)
    if fi is not None:
        for key in ("dividend_yield", "dividendYield"):
            try:
                val = fi[key] if not hasattr(fi, key) else getattr(fi, key)
            except Exception:
                val = None
            y = _f(val)
            if y > 0.0:
                return _normalize_yield(y)
    info = None
    try:
        info = tk.info
    except Exception:
        info = None
    if isinstance(info, dict):
        for key in ("dividendYield", "trailingAnnualDividendYield"):
            y = _f(info.get(key))
            if y > 0.0:
                return _normalize_yield(y)
    return 0.0


def _normalize_yield(y: float) -> float:
    """yfinance sometimes returns yield as a percent (e.g. 1.8 = 1.8%) and
    sometimes as a decimal (0.018). Normalize anything > 1 to a decimal."""
    return y / 100.0 if y > 1.0 else y


__all__ = [
    "ChainDataUnavailable",
    "LiquidityConfig",
    "DEFAULT_LIQUIDITY",
    "OptionQuote",
    "CatalystDates",
    "ChainSnapshot",
    "liquidity_score",
    "list_expiries",
    "fetch_chain",
    "fetch_catalysts",
]
