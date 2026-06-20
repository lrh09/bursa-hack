"""Core value objects and named-strategy constructors for the options toolkit.

This module is the single source of truth for the frozen value objects that
flow through the whole `bursahack.options` package (Leg / MarketState /
Position / Book / Account / AccountProfile and the data-only Greek containers
D1D2 / Greeks) PLUS the named-strategy constructors that assemble those legs
into a `Position` with signs, quantities, and per-leg multipliers set
correctly.

Sign & unit conventions (pinned once here, used everywhere)
-----------------------------------------------------------
  long qty  > 0          short qty < 0
  multiplier default 100 (PER LEG; Leg.mult), 1 for STOCK shares-as-units
  time everywhere in YEARS
  entry_price is the per-share fill premium MAGNITUDE (>= 0); the sign of the
    cash flow comes from qty (you PAY for longs, you RECEIVE for shorts)
  dollar_greek = per_share_greek * leg.mult * leg.qty            (signed)

Greek units (trader convention, filled by the pricer, not here):
  theta per calendar day (/365), vega per 1 vol point (/100), rho per 1% (/100).

Vertical-spread identity (locked by the tests)
----------------------------------------------
  For a one-wide vertical (one long + one short of the SAME right, qty equal &
  opposite), the maximum intrinsic value of the spread at expiry equals the
  strike WIDTH:  |K_short - K_long|.  A bull call (long lower / short higher)
  caps at +width; a bear call (short lower / long higher) caps at -width from
  the long holder's view, i.e. the credit collector keeps premium and the
  spread value bottoms at -width.

Synthetic-forward parity (locked by the tests)
----------------------------------------------
  A long synthetic forward = long call + short put at the SAME strike/expiry.
  Its expiry payoff is (S_T - K) for every S_T, identical to being long the
  underlying forward.  This is the structural restatement of put-call parity
  and is verified at the payoff level in the test module.

References:
  - Hull, "Options, Futures, and Other Derivatives" (combinations & parity).
  - BursaHack options BUILD CONTRACT, §2 (value objects) and §3 (structure).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

__all__ = [
    # enums
    "Right",
    "Style",
    "Measure",
    # data containers
    "D1D2",
    "Greeks",
    # value objects
    "Leg",
    "MarketState",
    "Position",
    "Book",
    "AccountProfile",
    "Account",
    # constructors
    "long_call",
    "long_put",
    "short_call",
    "short_put",
    "vertical",
    "bull_call_spread",
    "bear_call_spread",
    "bull_put_spread",
    "bear_put_spread",
    "straddle",
    "strangle",
    "iron_condor",
    "iron_butterfly",
    "butterfly",
    "calendar",
    "diagonal",
    "collar",
    "pmcc",
    "ratio_spread",
    "synthetic_long",
    "synthetic_short",
    "covered_call",
    "cash_secured_put",
    "from_legs",
]


# =============================================================================
# Enums
# =============================================================================
class Right(str, Enum):
    """Contract right. YAML carries the VALUE ("C"/"P"/"S"/"X").

    Construct from value: ``Right("C") -> Right.CALL``.
    """

    CALL = "C"
    PUT = "P"
    STOCK = "S"
    CASH = "X"


class Style(str, Enum):
    EUROPEAN = "european"
    AMERICAN = "american"


class Measure(str, Enum):
    RISK_NEUTRAL = "rn"
    REAL_WORLD = "rw"


# =============================================================================
# Data-only Greek containers (live here so tree pricers / book aggregation can
# return & consume them without pinning the whole tree to bsm).
# =============================================================================
@dataclass(frozen=True)
class D1D2:
    d1: float
    d2: float
    nd1: float  # n(d1) = norm_pdf(d1)
    Nd1: float  # N(d1)
    Nd2: float  # N(d2)


@dataclass(frozen=True)
class Greeks:
    """Full analytic greek surface in trader units.

    Fields a tree pricer cannot fill analytically are left ``None``
    (binomial fills higher orders via finite differences).
    """

    delta: float
    gamma: float
    theta_day: float
    theta_yr: float
    vega: float  # per 1 vol point
    rho: float  # per 1%
    vanna: float | None = None  # per vol point
    vomma: float | None = None  # per vol point^2
    veta: float | None = None
    ultima: float | None = None
    charm_day: float | None = None
    speed: float | None = None
    color_day: float | None = None
    zomma: float | None = None  # per vol point


# =============================================================================
# Value objects
# =============================================================================
@dataclass(frozen=True)
class Leg:
    right: Right
    strike: float  # 0.0 for STOCK / CASH
    expiry: date | None  # None for STOCK / CASH
    qty: float  # signed: +long / -short; contracts (or shares for STOCK)
    mult: int = 100  # 1 for STOCK shares-as-units; 100 for options
    entry_price: float = 0.0  # per-share fill premium magnitude (>= 0)
    style: Style = Style.EUROPEAN
    iv: float | None = None  # per-leg IV; None => use MarketState.sigma
    underlying: str = ""  # symbol; groups legs into a Position

    def __post_init__(self) -> None:
        # Light structural validation only — no math, no I/O.
        if not isinstance(self.right, Right):
            object.__setattr__(self, "right", Right(self.right))
        if not isinstance(self.style, Style):
            object.__setattr__(self, "style", Style(self.style))
        if self.qty == 0:
            raise ValueError("Leg.qty must be non-zero (signed: +long / -short).")
        if self.mult <= 0:
            raise ValueError("Leg.mult must be a positive multiplier.")
        if self.entry_price < 0:
            raise ValueError(
                "Leg.entry_price is a magnitude (>= 0); cash-flow sign comes from qty."
            )
        if self.right in (Right.CALL, Right.PUT) and self.strike <= 0:
            raise ValueError("Option legs require a positive strike.")

    # --- pure, sign-aware helpers (no market data) -------------------------
    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_short(self) -> bool:
        return self.qty < 0

    @property
    def sign(self) -> int:
        """+1 for long, -1 for short."""
        return 1 if self.qty > 0 else -1

    def signed_cashflow_entry(self) -> float:
        """Net entry-cost contribution for this leg (debit > 0, credit < 0).

        A long leg is a debit (you PAY -> positive cost); a short leg is a
        credit (you RECEIVE -> negative cost). Since ``qty`` already carries the
        sign and ``entry_price`` is a non-negative magnitude, the contribution
        is simply ``qty * entry_price * mult``. Pure: independent of the current
        market. This is the single building block ``net_cost_entry`` sums over,
        so summing leg contributions yields the net debit (>0) / credit (<0).
        """
        return self.qty * self.entry_price * self.mult

    def intrinsic(self, S: float) -> float:
        """Per-share intrinsic value at spot ``S`` (>= 0), unsigned.

        STOCK -> S; CASH -> 1.0 (a unit of cash); options -> max(S-K,0) /
        max(K-S,0). Pure intrinsic only; no time value.
        """
        if self.right is Right.CALL:
            return max(S - self.strike, 0.0)
        if self.right is Right.PUT:
            return max(self.strike - S, 0.0)
        if self.right is Right.STOCK:
            return S
        # CASH leg: each unit is worth 1.0 currency, qty already carries amount.
        return 1.0

    def signed_intrinsic_value(self, S: float) -> float:
        """Position-level intrinsic dollar value at expiry for this leg.

        ``intrinsic(S) * mult * qty`` — already signed by qty. This is the
        terminal (T->0) value contribution; net debit is subtracted by the
        caller so payoff and net-cost come from one source.
        """
        return self.intrinsic(S) * self.mult * self.qty


@dataclass(frozen=True)
class MarketState:
    spot: float
    r: float = 0.045
    q: float = 0.0
    sigma: float = 0.0  # flat fallback vol when a leg has no iv
    asof: datetime | None = None
    # ((t_years, cash_amt), ...) discrete dividends
    div_schedule: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError("MarketState.spot must be positive.")
        if self.q != 0.0 and self.div_schedule:
            raise ValueError(
                "A name has continuous q OR discrete div_schedule, never both."
            )


@dataclass(frozen=True)
class Position:
    underlying: str
    legs: tuple[Leg, ...]
    name: str = ""  # filled by structure.classify()

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("Position must have at least one leg.")
        # Normalise a list of legs to a tuple so the dataclass stays hashable.
        if not isinstance(self.legs, tuple):
            object.__setattr__(self, "legs", tuple(self.legs))

    @property
    def net_qty(self) -> float:
        """Signed sum of option/contract quantities (STOCK shares included)."""
        return sum(leg.qty for leg in self.legs)


@dataclass(frozen=True)
class Book:
    positions: tuple[Position, ...]
    asof: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.positions, tuple):
            object.__setattr__(self, "positions", tuple(self.positions))


@dataclass(frozen=True)
class AccountProfile:
    tax_status: str = "nra"  # 'us_person' | 'nra' | 'other'
    residence: str = "MY"
    broker: str = "IBKR"
    account_type: str = "portfolio_margin"  # 'cash'|'reg_t_margin'|'portfolio_margin'

    @staticmethod
    def rh_default() -> "AccountProfile":
        """RH's real status: NRA / Malaysia / IBKR / portfolio margin."""
        return AccountProfile(
            tax_status="nra",
            residence="MY",
            broker="IBKR",
            account_type="portfolio_margin",
        )

    def tax_rules(self) -> dict[str, bool]:
        """{'wash_sale','holding_split','us_cgt'} — all False for nra."""
        us = self.tax_status == "us_person"
        return {"wash_sale": us, "holding_split": us, "us_cgt": us}

    def standing_notes(self) -> list[str]:
        """Standing reminders tied to the account's tax/residence status.

        NRA (non-resident alien) trading US options falls under the 864(b)(2)
        trading safe-harbor (trading for one's own account is not a US trade or
        business); US estate-situs treatment of options is a grey area; no US
        capital-gains tax on the trading P&L. Informational only — not advice.
        """
        notes: list[str] = []
        if self.tax_status == "nra":
            notes.append(
                "NRA 864(b)(2) trading safe-harbor: trading for own account is "
                "not a US trade or business — no US CGT on the P&L."
            )
            notes.append(
                "US estate-situs of options is a grey area for NRAs; size with "
                "that uncertainty in mind."
            )
            notes.append(
                "US wash-sale and short/long holding-period splits do NOT apply "
                "to an NRA — those checks are informational only."
            )
        elif self.tax_status == "us_person":
            notes.append(
                "US person: wash-sale (61-day window) and holding-period splits "
                "DO apply; track lots accordingly."
            )
        else:
            notes.append(
                "Unrecognised tax_status — treat tax mechanics as unknown and "
                "consult a professional."
            )
        return notes


@dataclass(frozen=True)
class Account:
    profile: AccountProfile
    cash: float
    netliq: float  # caller-supplied NLV; the ONE source for netliq across book/margin


# =============================================================================
# Named-strategy constructors
# -----------------------------------------------------------------------------
# Each returns a Position with signs / qty / mult set correctly. They are the
# inverse of classify(): builder(...).name is filled by classify() downstream,
# so these leave name="".  All option legs default to mult=100; STOCK legs to
# mult=1 (shares-as-units).  entry_* args are per-share fill MAGNITUDES.
# =============================================================================
def _opt(
    underlying: str,
    right: Right,
    strike: float,
    expiry: date | None,
    qty: float,
    mult: int = 100,
    entry: float = 0.0,
    style: Style = Style.EUROPEAN,
    iv: float | None = None,
) -> Leg:
    """Internal: build a single option Leg with the package conventions."""
    return Leg(
        right=right,
        strike=float(strike),
        expiry=expiry,
        qty=float(qty),
        mult=mult,
        entry_price=float(entry),
        style=style,
        iv=iv,
        underlying=underlying,
    )


def _stock(underlying: str, shares: float, entry: float = 0.0) -> Leg:
    """Internal: build a STOCK leg (mult=1, shares-as-units)."""
    return Leg(
        right=Right.STOCK,
        strike=0.0,
        expiry=None,
        qty=float(shares),
        mult=1,
        entry_price=float(entry),
        style=Style.EUROPEAN,
        iv=None,
        underlying=underlying,
    )


# --- single legs -----------------------------------------------------------
def long_call(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    mult: int = 100,
    entry: float = 0.0,
    style: Style = Style.EUROPEAN,
    iv: float | None = None,
) -> Position:
    return Position(
        underlying,
        (_opt(underlying, Right.CALL, K, expiry, abs(qty), mult, entry, style, iv),),
    )


def long_put(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    mult: int = 100,
    entry: float = 0.0,
    style: Style = Style.EUROPEAN,
    iv: float | None = None,
) -> Position:
    return Position(
        underlying,
        (_opt(underlying, Right.PUT, K, expiry, abs(qty), mult, entry, style, iv),),
    )


def short_call(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    mult: int = 100,
    entry: float = 0.0,
    style: Style = Style.EUROPEAN,
    iv: float | None = None,
) -> Position:
    return Position(
        underlying,
        (_opt(underlying, Right.CALL, K, expiry, -abs(qty), mult, entry, style, iv),),
    )


def short_put(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    mult: int = 100,
    entry: float = 0.0,
    style: Style = Style.EUROPEAN,
    iv: float | None = None,
) -> Position:
    return Position(
        underlying,
        (_opt(underlying, Right.PUT, K, expiry, -abs(qty), mult, entry, style, iv),),
    )


# --- verticals -------------------------------------------------------------
def vertical(
    underlying: str,
    K_long: float,
    K_short: float,
    expiry: date | None,
    right: Right,
    qty: float = 1,
    entry_long: float = 0.0,
    entry_short: float = 0.0,
    mult: int = 100,
) -> Position:
    """Generic vertical: long one strike, short another, SAME right & expiry.

    Debit vs credit is determined by the strikes and the right (auto). The
    spread WIDTH is ``abs(K_short - K_long)`` and the maximum intrinsic value
    of the spread equals that width — locked by the tests.
    """
    right = Right(right) if not isinstance(right, Right) else right
    q = abs(qty)
    legs = (
        _opt(underlying, right, K_long, expiry, q, mult, entry_long),
        _opt(underlying, right, K_short, expiry, -q, mult, entry_short),
    )
    return Position(underlying, legs)


def bull_call_spread(
    underlying: str,
    K_long: float,
    K_short: float,
    expiry: date | None,
    qty: float = 1,
    entry_long: float = 0.0,
    entry_short: float = 0.0,
    mult: int = 100,
) -> Position:
    """Long lower call + short higher call (debit). Max value = width.

    GOLDEN: bull_call_spread('TSLA',360,460,exp,qty=8,entry_long=91.96,
    entry_short=53.00).
    """
    if K_long >= K_short:
        raise ValueError("bull_call_spread requires K_long < K_short.")
    return vertical(
        underlying,
        K_long,
        K_short,
        expiry,
        Right.CALL,
        qty=qty,
        entry_long=entry_long,
        entry_short=entry_short,
        mult=mult,
    )


def bear_call_spread(
    underlying: str,
    K_short: float,
    K_long: float,
    expiry: date | None,
    qty: float = 1,
    entry_short: float = 0.0,
    entry_long: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short lower call + long higher call (credit). 500/530 golden.

    Args are (K_short, K_long) — the short (lower) strike is collected, the
    long (higher) strike is the protective wing.
    """
    if K_short >= K_long:
        raise ValueError("bear_call_spread requires K_short < K_long.")
    q = abs(qty)
    legs = (
        _opt(underlying, Right.CALL, K_short, expiry, -q, mult, entry_short),
        _opt(underlying, Right.CALL, K_long, expiry, q, mult, entry_long),
    )
    return Position(underlying, legs)


def bull_put_spread(
    underlying: str,
    K_short: float,
    K_long: float,
    expiry: date | None,
    qty: float = 1,
    entry_short: float = 0.0,
    entry_long: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short higher put + long lower put (credit / bullish)."""
    if K_short <= K_long:
        raise ValueError("bull_put_spread requires K_short > K_long.")
    q = abs(qty)
    legs = (
        _opt(underlying, Right.PUT, K_short, expiry, -q, mult, entry_short),
        _opt(underlying, Right.PUT, K_long, expiry, q, mult, entry_long),
    )
    return Position(underlying, legs)


def bear_put_spread(
    underlying: str,
    K_long: float,
    K_short: float,
    expiry: date | None,
    qty: float = 1,
    entry_long: float = 0.0,
    entry_short: float = 0.0,
    mult: int = 100,
) -> Position:
    """Long higher put + short lower put (debit / bearish)."""
    if K_long <= K_short:
        raise ValueError("bear_put_spread requires K_long > K_short.")
    return vertical(
        underlying,
        K_long,
        K_short,
        expiry,
        Right.PUT,
        qty=qty,
        entry_long=entry_long,
        entry_short=entry_short,
        mult=mult,
    )


# --- volatility two-leggers ------------------------------------------------
def straddle(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    side: str = "long",
    entry_call: float = 0.0,
    entry_put: float = 0.0,
    mult: int = 100,
) -> Position:
    """Call + put at the SAME strike/expiry. side='long' (debit) or 'short'."""
    s = 1 if side == "long" else -1
    q = s * abs(qty)
    legs = (
        _opt(underlying, Right.CALL, K, expiry, q, mult, entry_call),
        _opt(underlying, Right.PUT, K, expiry, q, mult, entry_put),
    )
    return Position(underlying, legs)


def strangle(
    underlying: str,
    K_put: float,
    K_call: float,
    expiry: date | None,
    qty: float = 1,
    side: str = "long",
    entry_put: float = 0.0,
    entry_call: float = 0.0,
    mult: int = 100,
) -> Position:
    """OTM put (lower) + OTM call (higher). side='long' or 'short'."""
    if K_put >= K_call:
        raise ValueError("strangle requires K_put < K_call.")
    s = 1 if side == "long" else -1
    q = s * abs(qty)
    legs = (
        _opt(underlying, Right.PUT, K_put, expiry, q, mult, entry_put),
        _opt(underlying, Right.CALL, K_call, expiry, q, mult, entry_call),
    )
    return Position(underlying, legs)


# --- four-leggers ----------------------------------------------------------
def iron_condor(
    underlying: str,
    put_long: float,
    put_short: float,
    call_short: float,
    call_long: float,
    expiry: date | None,
    qty: float = 1,
    entry_put_long: float = 0.0,
    entry_put_short: float = 0.0,
    entry_call_short: float = 0.0,
    entry_call_long: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short strangle wrapped by long wings (net credit, defined risk).

    Strikes ascend: put_long < put_short < call_short < call_long.
    """
    if not (put_long < put_short < call_short < call_long):
        raise ValueError(
            "iron_condor requires put_long < put_short < call_short < call_long."
        )
    q = abs(qty)
    legs = (
        _opt(underlying, Right.PUT, put_long, expiry, q, mult, entry_put_long),
        _opt(underlying, Right.PUT, put_short, expiry, -q, mult, entry_put_short),
        _opt(underlying, Right.CALL, call_short, expiry, -q, mult, entry_call_short),
        _opt(underlying, Right.CALL, call_long, expiry, q, mult, entry_call_long),
    )
    return Position(underlying, legs)


def iron_butterfly(
    underlying: str,
    put_long: float,
    body: float,
    call_long: float,
    expiry: date | None,
    qty: float = 1,
    entry_put_long: float = 0.0,
    entry_put_body: float = 0.0,
    entry_call_body: float = 0.0,
    entry_call_long: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short ATM straddle (at ``body``) wrapped by long wings.

    Strikes ascend: put_long < body < call_long.
    """
    if not (put_long < body < call_long):
        raise ValueError("iron_butterfly requires put_long < body < call_long.")
    q = abs(qty)
    legs = (
        _opt(underlying, Right.PUT, put_long, expiry, q, mult, entry_put_long),
        _opt(underlying, Right.PUT, body, expiry, -q, mult, entry_put_body),
        _opt(underlying, Right.CALL, body, expiry, -q, mult, entry_call_body),
        _opt(underlying, Right.CALL, call_long, expiry, q, mult, entry_call_long),
    )
    return Position(underlying, legs)


def butterfly(
    underlying: str,
    K_low: float,
    K_mid: float,
    K_high: float,
    expiry: date | None,
    right: Right,
    qty: float = 1,
    side: str = "long",
    entry_low: float = 0.0,
    entry_mid: float = 0.0,
    entry_high: float = 0.0,
    mult: int = 100,
) -> Position:
    """Single-right butterfly: +1 low, -2 mid, +1 high (long body=debit).

    side='long' -> +1/-2/+1; side='short' flips all signs.
    """
    right = Right(right) if not isinstance(right, Right) else right
    if not (K_low < K_mid < K_high):
        raise ValueError("butterfly requires K_low < K_mid < K_high.")
    s = 1 if side == "long" else -1
    q = abs(qty)
    legs = (
        _opt(underlying, right, K_low, expiry, s * q, mult, entry_low),
        _opt(underlying, right, K_mid, expiry, -2 * s * q, mult, entry_mid),
        _opt(underlying, right, K_high, expiry, s * q, mult, entry_high),
    )
    return Position(underlying, legs)


# --- time spreads ----------------------------------------------------------
def calendar(
    underlying: str,
    K: float,
    near_exp: date | None,
    far_exp: date | None,
    right: Right,
    qty: float = 1,
    entry_near: float = 0.0,
    entry_far: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short near-dated + long far-dated, SAME strike & right (debit)."""
    right = Right(right) if not isinstance(right, Right) else right
    q = abs(qty)
    legs = (
        _opt(underlying, right, K, near_exp, -q, mult, entry_near),
        _opt(underlying, right, K, far_exp, q, mult, entry_far),
    )
    return Position(underlying, legs)


def diagonal(
    underlying: str,
    K_near: float,
    near_exp: date | None,
    K_far: float,
    far_exp: date | None,
    right: Right,
    qty: float = 1,
    entry_near: float = 0.0,
    entry_far: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short near-dated strike + long far-dated strike, SAME right.

    A calendar with different strikes (different time AND moneyness).
    """
    right = Right(right) if not isinstance(right, Right) else right
    q = abs(qty)
    legs = (
        _opt(underlying, right, K_near, near_exp, -q, mult, entry_near),
        _opt(underlying, right, K_far, far_exp, q, mult, entry_far),
    )
    return Position(underlying, legs)


# --- stock-overlay structures ---------------------------------------------
def collar(
    underlying: str,
    shares: float,
    put_K: float,
    call_K: float,
    expiry: date | None,
    entry_stock: float = 0.0,
    entry_put: float = 0.0,
    entry_call: float = 0.0,
    mult: int = 100,
) -> Position:
    """Long stock + protective long put + financing short call.

    One options contract per ``mult`` shares: contracts = shares / mult.
    Requires put_K < call_K (protect below, cap above).
    """
    if put_K >= call_K:
        raise ValueError("collar requires put_K < call_K.")
    contracts = abs(shares) / mult
    legs = (
        _stock(underlying, abs(shares), entry_stock),
        _opt(underlying, Right.PUT, put_K, expiry, contracts, mult, entry_put),
        _opt(underlying, Right.CALL, call_K, expiry, -contracts, mult, entry_call),
    )
    return Position(underlying, legs)


def covered_call(
    underlying: str,
    shares: float,
    call_K: float,
    expiry: date | None,
    entry_stock: float = 0.0,
    entry_call: float = 0.0,
    mult: int = 100,
) -> Position:
    """Long stock + short call (one contract per ``mult`` shares)."""
    contracts = abs(shares) / mult
    legs = (
        _stock(underlying, abs(shares), entry_stock),
        _opt(underlying, Right.CALL, call_K, expiry, -contracts, mult, entry_call),
    )
    return Position(underlying, legs)


def cash_secured_put(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    entry: float = 0.0,
    mult: int = 100,
) -> Position:
    """Short put backed by cash (a single short-put Position)."""
    return short_put(underlying, K, expiry, qty=qty, mult=mult, entry=entry)


def pmcc(
    underlying: str,
    leap_K: float,
    leap_exp: date | None,
    short_K: float,
    short_exp: date | None,
    qty: float = 1,
    entry_leap: float = 0.0,
    entry_short: float = 0.0,
    mult: int = 100,
) -> Position:
    """Poor-man's covered call: long deep-ITM LEAP call + short near OTM call.

    short_K should sit above leap_K; near expiry < far expiry.
    """
    q = abs(qty)
    legs = (
        _opt(underlying, Right.CALL, leap_K, leap_exp, q, mult, entry_leap),
        _opt(underlying, Right.CALL, short_K, short_exp, -q, mult, entry_short),
    )
    return Position(underlying, legs)


# --- ratio & synthetic -----------------------------------------------------
def ratio_spread(
    underlying: str,
    K_long: float,
    K_short: float,
    expiry: date | None,
    right: Right,
    long_qty: float = 1,
    short_qty: float = 2,
    entry_long: float = 0.0,
    entry_short: float = 0.0,
    mult: int = 100,
) -> Position:
    """Ratio spread: ``long_qty`` long + ``short_qty`` short of the SAME right.

    Classic 1x2 ratio: long_qty=1, short_qty=2 -> a naked-tail structure (the
    extra short leg leaves an unbounded side; defined-risk gate downstream).
    """
    right = Right(right) if not isinstance(right, Right) else right
    legs = (
        _opt(underlying, right, K_long, expiry, abs(long_qty), mult, entry_long),
        _opt(underlying, right, K_short, expiry, -abs(short_qty), mult, entry_short),
    )
    return Position(underlying, legs)


def synthetic_long(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    entry_call: float = 0.0,
    entry_put: float = 0.0,
    mult: int = 100,
) -> Position:
    """Synthetic long forward = long call + short put, SAME strike/expiry.

    Expiry payoff is (S_T - K) for all S_T — verified at the payoff level
    (put-call parity restated).
    """
    q = abs(qty)
    legs = (
        _opt(underlying, Right.CALL, K, expiry, q, mult, entry_call),
        _opt(underlying, Right.PUT, K, expiry, -q, mult, entry_put),
    )
    return Position(underlying, legs)


def synthetic_short(
    underlying: str,
    K: float,
    expiry: date | None,
    qty: float = 1,
    entry_call: float = 0.0,
    entry_put: float = 0.0,
    mult: int = 100,
) -> Position:
    """Synthetic short forward = short call + long put, SAME strike/expiry.

    Expiry payoff is (K - S_T) for all S_T.
    """
    q = abs(qty)
    legs = (
        _opt(underlying, Right.CALL, K, expiry, -q, mult, entry_call),
        _opt(underlying, Right.PUT, K, expiry, q, mult, entry_put),
    )
    return Position(underlying, legs)


# --- escape hatch ----------------------------------------------------------
def from_legs(underlying: str, legs: list[Leg]) -> Position:
    """Wrap an arbitrary list of legs into a Position (custom structures)."""
    return Position(underlying, tuple(legs))
