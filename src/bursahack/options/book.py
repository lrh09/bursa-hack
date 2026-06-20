"""Book aggregation -- the whole-book risk lens.

This is NOT bursahack.portfolio (that is the unrelated backtest weights/shares
engine). This module rolls a Book of option Positions into net & dollar greeks,
net delta-equivalent shares, beta-weighted delta to SPY, concentration vs NetLiq,
a whole-book naked-tail scan (L7 source of truth -- it catches hedges that span
two positions), and an income-posture verdict.

Dollar-greek convention (L5): per-share greek * leg.mult * leg.qty, already summed
inside position.dollar_greeks. We aggregate those signed dollar greeks across the
whole book. Net delta-equivalent shares = sum over legs of delta * mult * qty
(option deltas already in per-share terms, * 100 * contracts = share-equivalent).

netliq single source (P4): every NetLiq-relative metric takes netliq from
Account.netliq via the caller; this module never recomputes NLV.

References:
  - Beta-weighted delta: tastytrade / Sinclair "Positional Option Trading".
  - HHI concentration: Herfindahl-Hirschman Index on |notional| shares.
"""
from __future__ import annotations

from dataclasses import dataclass

from bursahack.options import position as _position
from bursahack.options.types import Book, Greeks, MarketState, Position, Right

# Dollar-greek keys we roll up at book level (theta already per-day, vega per vol
# point, rho per 1% -- per the trader-units convention in types.py).
_DOLLAR_KEYS = ("delta", "gamma", "theta_day", "vega", "rho")


@dataclass(frozen=True)
class GreeksReport:
    per_underlying: dict
    totals: dict   # delta_sh, dollar_delta, dollar_gamma_1pct, dollar_vega, dollar_theta_day, dollar_rho_1pct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _market_for(name: str, market_by_name: dict[str, MarketState]) -> MarketState:
    """Resolve a position's MarketState; KeyError surfaces a real config gap."""
    if name not in market_by_name:
        raise KeyError(
            f"no MarketState supplied for underlying {name!r}; "
            f"have {sorted(market_by_name)}"
        )
    return market_by_name[name]


def _position_dollar_greeks(pos: Position, market: MarketState) -> dict[str, float]:
    """Signed dollar greeks for one position (already * mult * qty, summed)."""
    return _position.dollar_greeks(list(pos.legs), market.spot, market)


def _position_delta_shares(pos: Position, market: MarketState) -> float:
    """Net delta-equivalent SHARES for one position.

    dollar_greeks['delta'] is sum(per_share_delta * mult * qty) -- i.e. already the
    share-equivalent delta. STOCK legs contribute qty*mult (delta 1) automatically
    through value_position's greek path.
    """
    dg = _position.dollar_greeks(list(pos.legs), market.spot, market)
    return float(dg.get("delta", 0.0))


# ---------------------------------------------------------------------------
# Net / dollar greeks
# ---------------------------------------------------------------------------


def net_greeks(book: Book, market_by_name: dict[str, MarketState]) -> GreeksReport:
    """Roll the whole book into per-underlying and total dollar greeks.

    per_underlying[symbol] -> {delta_sh, dollar_delta, dollar_gamma_1pct,
        dollar_vega, dollar_theta_day, dollar_rho_1pct}
    totals -> same keys, summed across underlyings.

    dollar_delta = delta_sh * spot   (P&L for a $1 spot move is delta_sh;
        dollar_delta is the notional the delta represents).
    dollar_gamma_1pct = gamma_$ * (0.01 * spot)^2 ... we report gamma's $ P&L for a
        1% spot move: 0.5 * gamma_sh * (0.01*S)^2 is the convexity term; we expose
        the linear-per-1% form gamma_sh * (0.01*S) so it reads as "delta change per
        1% move", which is what a desk scans. Keyed dollar_gamma_1pct.
    """
    per: dict[str, dict] = {}
    for pos in book.positions:
        mkt = _market_for(pos.underlying, market_by_name)
        dg = _position_dollar_greeks(pos, mkt)
        S = mkt.spot
        delta_sh = float(dg.get("delta", 0.0))
        gamma_sh = float(dg.get("gamma", 0.0))
        bucket = per.setdefault(pos.underlying, {
            "delta_sh": 0.0,
            "dollar_delta": 0.0,
            "dollar_gamma_1pct": 0.0,
            "dollar_vega": 0.0,
            "dollar_theta_day": 0.0,
            "dollar_rho_1pct": 0.0,
        })
        bucket["delta_sh"] += delta_sh
        bucket["dollar_delta"] += delta_sh * S
        bucket["dollar_gamma_1pct"] += gamma_sh * (0.01 * S)
        bucket["dollar_vega"] += float(dg.get("vega", 0.0))
        bucket["dollar_theta_day"] += float(dg.get("theta_day", 0.0))
        bucket["dollar_rho_1pct"] += float(dg.get("rho", 0.0))

    totals = {
        "delta_sh": 0.0,
        "dollar_delta": 0.0,
        "dollar_gamma_1pct": 0.0,
        "dollar_vega": 0.0,
        "dollar_theta_day": 0.0,
        "dollar_rho_1pct": 0.0,
    }
    for bucket in per.values():
        for k in totals:
            totals[k] += bucket[k]

    return GreeksReport(per_underlying=per, totals=totals)


# ---------------------------------------------------------------------------
# Delta-equivalent shares & notional
# ---------------------------------------------------------------------------


def delta_equiv(book: Book, market_by_name: dict[str, MarketState]) -> dict:
    """Per-name + total delta-equivalent shares and notionals.

    Returns:
        {
          'per_underlying': {sym: {delta_equiv_shares, net_notional, gross_notional}},
          'total': {delta_equiv_shares, net_notional, gross_notional},
        }
    net_notional = delta_equiv_shares * spot (signed exposure).
    gross_notional = sum of |leg| share-equivalent notionals (absolute footprint).
    """
    per: dict[str, dict] = {}
    for pos in book.positions:
        mkt = _market_for(pos.underlying, market_by_name)
        S = mkt.spot
        des = _position_delta_shares(pos, mkt)
        gross = 0.0
        for leg in pos.legs:
            gross += abs(leg.qty) * leg.mult * S
        bucket = per.setdefault(pos.underlying, {
            "delta_equiv_shares": 0.0,
            "net_notional": 0.0,
            "gross_notional": 0.0,
        })
        bucket["delta_equiv_shares"] += des
        bucket["net_notional"] += des * S
        bucket["gross_notional"] += gross

    total = {"delta_equiv_shares": 0.0, "net_notional": 0.0, "gross_notional": 0.0}
    for bucket in per.values():
        for k in total:
            total[k] += bucket[k]

    return {"per_underlying": per, "total": total}


def beta_weighted_delta(book: Book, market_by_name: dict[str, MarketState],
                        betas: dict, S_spy: float) -> dict:
    """Beta-weight every name's delta-equivalent dollars back to SPY.

    bw_delta_dollars(name) = delta_$(name) * beta(name)
    bw_delta_spy_shares    = sum(bw_delta_dollars) / S_spy

    A name missing from `betas` defaults to beta 1.0 (flagged in 'missing_betas').

    Returns:
        {
          'per_underlying': {sym: {beta, delta_dollars, bw_delta_dollars}},
          'total_bw_delta_dollars': float,
          'bw_delta_spy_shares': float,     # SPY-share equivalent of the whole book
          'missing_betas': [sym, ...],
        }
    """
    de = delta_equiv(book, market_by_name)["per_underlying"]
    per: dict[str, dict] = {}
    missing: list[str] = []
    total_bw = 0.0
    for sym, bucket in de.items():
        beta = betas.get(sym)
        if beta is None:
            beta = 1.0
            missing.append(sym)
        delta_dollars = bucket["net_notional"]   # delta_equiv_shares * spot
        bw = delta_dollars * float(beta)
        per[sym] = {
            "beta": float(beta),
            "delta_dollars": float(delta_dollars),
            "bw_delta_dollars": float(bw),
        }
        total_bw += bw

    bw_spy_shares = (total_bw / S_spy) if S_spy else 0.0
    return {
        "per_underlying": per,
        "total_bw_delta_dollars": float(total_bw),
        "bw_delta_spy_shares": float(bw_spy_shares),
        "missing_betas": missing,
    }


# ---------------------------------------------------------------------------
# Concentration vs NetLiq (+ HHI)
# ---------------------------------------------------------------------------


def concentration(book: Book, market_by_name: dict[str, MarketState],
                  netliq: float, thresholds: dict | None = None) -> dict:
    """Per-name gross-notional concentration vs NetLiq, plus the book HHI.

    netliq is authoritative from Account.netliq (P4) -- never recomputed here.
    HHI = sum(share_i^2) over name gross-notional shares (0..1; 1 = single name).

    thresholds (optional) gates the 'flags' list:
        {'name_pct': 0.25, 'hhi': 0.30} -> flag any name above 25% of NetLiq and
        flag the whole book if HHI > 0.30. Defaults applied when omitted.

    Returns:
        {
          'per_underlying': {sym: {gross_notional, pct_of_netliq}},
          'hhi': float,
          'netliq': float,
          'flags': [str, ...],
        }
    """
    thresholds = thresholds or {}
    name_cap = float(thresholds.get("name_pct", 0.25))
    hhi_cap = float(thresholds.get("hhi", 0.30))

    de = delta_equiv(book, market_by_name)["per_underlying"]
    gross_by_name = {sym: bucket["gross_notional"] for sym, bucket in de.items()}
    gross_total = sum(gross_by_name.values())

    per: dict[str, dict] = {}
    for sym, gross in gross_by_name.items():
        per[sym] = {
            "gross_notional": float(gross),
            "pct_of_netliq": (gross / netliq) if netliq and netliq > 0 else None,
        }

    if gross_total > 0:
        hhi = sum((g / gross_total) ** 2 for g in gross_by_name.values())
    else:
        hhi = 0.0

    flags: list[str] = []
    if netliq and netliq > 0:
        for sym, info in per.items():
            if info["pct_of_netliq"] is not None and info["pct_of_netliq"] > name_cap:
                flags.append(
                    f"{sym} gross notional is {info['pct_of_netliq']:.0%} of NetLiq "
                    f"(> {name_cap:.0%} cap)"
                )
    if hhi > hhi_cap:
        flags.append(f"book HHI {hhi:.2f} exceeds {hhi_cap:.2f} concentration cap")

    return {
        "per_underlying": per,
        "hhi": float(hhi),
        "netliq": float(netliq),
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Whole-book naked-tail scan (L7 source of truth)
# ---------------------------------------------------------------------------


def _net_call_put_qty(book: Book) -> dict[str, dict[str, float]]:
    """Per-underlying net signed contract quantity for calls and for puts, summed
    across ALL positions (so a short call in one position offset by a long call in
    another nets out -- the cross-position hedge L7 demands we catch)."""
    agg: dict[str, dict[str, float]] = {}
    for pos in book.positions:
        for leg in pos.legs:
            if leg.right not in (Right.CALL, Right.PUT):
                continue
            bucket = agg.setdefault(pos.underlying, {"call": 0.0, "put": 0.0})
            key = "call" if leg.right is Right.CALL else "put"
            bucket[key] += leg.qty
    return agg


def naked_scan(book: Book, market_by_name: dict[str, MarketState]) -> dict:
    """Whole-book naked-tail truth (L7).

    For each underlying we net call and put contracts across the ENTIRE book, then:
      - net short calls (call_qty < 0 not covered by long calls of >= count and a
        higher strike, conservatively flagged on net sign) -> UNBOUNDED UP tail.
      - net short puts (put_qty < 0) -> large DOWN tail (bounded at strike->0 but a
        defined-risk gate still flags it as a naked-put exposure).

    This is intentionally a NET-SIGN scan at the book level: it is the conservative
    cross-position truth that catches a short call in one position hedged by a long
    call in another (they net to non-naked). Strike-laddered exact coverage is the
    per-position risk_shape's job; this is the book-wide gate.

    Returns:
        {
          'per_underlying': {sym: {net_calls, net_puts, naked_up, naked_down}},
          'naked_up': [sym, ...],     # unbounded up-tail names
          'naked_down': [sym, ...],   # large down-tail (naked put / net short stock-like)
          'any_unbounded': bool,
          'notes': [str, ...],
        }
    """
    agg = _net_call_put_qty(book)
    per: dict[str, dict] = {}
    up: list[str] = []
    down: list[str] = []
    notes: list[str] = []

    for sym, bucket in agg.items():
        net_calls = bucket["call"]
        net_puts = bucket["put"]
        naked_up = net_calls < 0       # net short calls -> unbounded up tail
        naked_down = net_puts < 0      # net short puts -> large down tail
        per[sym] = {
            "net_calls": net_calls,
            "net_puts": net_puts,
            "naked_up": naked_up,
            "naked_down": naked_down,
        }
        if naked_up:
            up.append(sym)
            notes.append(f"{sym}: net {net_calls:+.0f} calls -> UNBOUNDED up-tail (naked short calls)")
        if naked_down:
            down.append(sym)
            notes.append(f"{sym}: net {net_puts:+.0f} puts -> large down-tail (naked short puts)")

    return {
        "per_underlying": per,
        "naked_up": up,
        "naked_down": down,
        "any_unbounded": bool(up),   # only the up-tail is truly unbounded
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Income posture verdict
# ---------------------------------------------------------------------------


def income_posture(book: Book, market_by_name: dict[str, MarketState],
                   betas: dict, S_spy: float, netliq: float,
                   cfg: dict | None = None) -> dict:
    """Income-overlay posture for the whole book.

    Reads the book's net beta-weighted (SPY-share) delta and recommends the credit
    side that TRIMS exposure, while explicitly rejecting (with a reason) the side
    that would ADD correlated delta.

    Behaviour (the §7 golden): an over-long book (net long SPY-delta above the band)
      -> recommend a CALL credit spread (negative delta, trims the long)
      -> REJECT a put credit spread (positive delta, adds correlated long delta =
         the book's worst day).
    Symmetrically, an over-short book recommends a PUT credit spread and rejects a
    call credit spread.

    cfg:
        {'band_spy_shares': 50.0}  -> dead-band half-width in SPY-share equivalents.

    Returns:
        {
          'posture': 'over_long' | 'over_short' | 'balanced',
          'net_delta_sh': float,            # raw book delta-equiv shares
          'bw_delta_spy_shares': float,     # SPY-share equivalent (beta-weighted)
          'recommended_side': 'call_credit' | 'put_credit' | None,
          'anti_recommendation': 'call_credit' | 'put_credit' | None,
          'anti_reason': str | None,
          'target_delta_band': (lo, hi),    # in SPY-share equivalents
        }
    """
    cfg = cfg or {}
    band = float(cfg.get("band_spy_shares", 50.0))

    de_total = delta_equiv(book, market_by_name)["total"]
    bw = beta_weighted_delta(book, market_by_name, betas, S_spy)
    net_delta_sh = float(de_total["delta_equiv_shares"])
    bw_spy = float(bw["bw_delta_spy_shares"])

    lo, hi = -band, band
    if bw_spy > hi:
        posture = "over_long"
        recommended = "call_credit"
        anti = "put_credit"
        anti_reason = (
            "a put credit spread is net long delta -- it ADDS correlated long "
            "exposure to an already over-long book (the book's worst day gets worse); "
            "sell call credit spreads to trim delta instead."
        )
    elif bw_spy < lo:
        posture = "over_short"
        recommended = "put_credit"
        anti = "call_credit"
        anti_reason = (
            "a call credit spread is net short delta -- it ADDS correlated short "
            "exposure to an already over-short book; sell put credit spreads to "
            "trim the short instead."
        )
    else:
        posture = "balanced"
        recommended = None
        anti = None
        anti_reason = None

    return {
        "posture": posture,
        "net_delta_sh": net_delta_sh,
        "bw_delta_spy_shares": bw_spy,
        "recommended_side": recommended,
        "anti_recommendation": anti,
        "anti_reason": anti_reason,
        "target_delta_band": (lo, hi),
    }


# Greeks is re-exported for type clarity to consumers building per-name reports.
__all__ = [
    "GreeksReport",
    "Greeks",
    "net_greeks",
    "delta_equiv",
    "beta_weighted_delta",
    "concentration",
    "naked_scan",
    "income_posture",
]
