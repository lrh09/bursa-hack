"""Position-level valuation kernel: net cost, mark-to-model value, and dollar
greeks for a multi-leg option position.

This is the L3 compute kernel -- the single *repricing* path the whole toolkit
marks against. The scenario engine, the book aggregator, the charts surfaces and
the probability/Monte-Carlo modules all route their "value the structure now / at
a perturbed state" question through here, so there is one valuation truth and the
T->0 limit of ``value_position`` is exactly the terminal payoff in ``payoff``.

What lives here
---------------
  * ``net_cost_entry``  -- realised entry cash (debit > 0 / credit < 0).
  * ``net_cost_fair``   -- theoretical entry-equivalent cash at current model fair
                           value (for "what if I opened this today" framing).
  * ``value_position``  -- signed mark-to-model dollar value at a spot, with time
                           decayed by ``dt_days`` and an optional flat-vol override.
  * ``dollar_greeks``   -- signed dollar greeks (per-share greek * mult * qty),
                           summed across legs, as a ``dict[str, float]``.
  * ``position_greeks`` -- per-share aggregate greeks (no qty/mult scaling).
  * ``pnl_now``         -- ``value_position - net_cost_entry`` convenience.

Sign / unit conventions (pinned in ``instruments``)
---------------------------------------------------
  * long qty > 0, short qty < 0; per-leg multiplier ``leg.mult`` (1 for STOCK).
  * time in YEARS internally; scenario ``dt_days`` are CALENDAR days, so the
    decayed time is ``T' = max((expiry - asof).days - dt_days, 0) / 365``.
  * effective vol per leg = ``iv`` override (if given) else ``leg.iv`` else
    ``market.sigma``.
  * dollar_greek = per_share_greek * leg.mult * leg.qty (already signed).

Pricing routing (design law L1, one pricing core)
-------------------------------------------------
  * EUROPEAN legs -> ``bs.price`` / ``bs.all_greeks``.
  * AMERICAN legs (or any leg under a discrete cash-dividend schedule) ->
    ``american.crr_price`` / ``american.price_discrete_div``; greeks for those
    legs come from ``bs.all_greeks`` (the analytic surface) as the trader-unit
    sensitivity proxy, consistent with the rest of the toolkit.

References:
  - Hull, *Options, Futures, and Other Derivatives* (mark-to-model, greeks).
  - BursaHack options BUILD CONTRACT, §3 (structure) and L3 (single payoff path).
"""
from __future__ import annotations

from collections.abc import Sequence

from bursahack.options import american as _american
from bursahack.options import bs as _bs
from bursahack.options.instruments import Leg, MarketState, Right, Style

__all__ = [
    "net_cost_entry",
    "net_cost_fair",
    "value_position",
    "dollar_greeks",
    "position_greeks",
    "pnl_now",
]

# Dollar-greek keys rolled up at position level (trader units: theta per day &
# per year, vega per vol point, rho per 1%). Higher-order keys are filled when
# the analytic surface provides them.
_FIRST_ORDER_KEYS = ("delta", "gamma", "theta_day", "theta_yr", "vega", "rho")
_HIGHER_ORDER_KEYS = (
    "vanna", "vomma", "veta", "ultima", "charm_day", "speed", "color_day", "zomma",
)


# ---------------------------------------------------------------------------
# Per-leg time & vol resolution (shared by value & greeks)
# ---------------------------------------------------------------------------
def _leg_T(leg: Leg, mkt: MarketState, dt_days: float) -> float:
    """Years to expiry for ``leg`` as seen from ``mkt.asof``, decayed ``dt_days``.

    ``T' = max((expiry - asof).days, 0) / 365 - dt_days / 365``, clamped at >= 0.
    STOCK / CASH legs (no expiry) and a missing ``asof`` yield 0.0 (they have no
    optional time value to decay).
    """
    if leg.expiry is None or mkt.asof is None:
        return 0.0
    days = (leg.expiry - mkt.asof.date()).days
    T = max(days, 0) / 365.0 - float(dt_days) / 365.0
    return T if T > 0.0 else 0.0


def _leg_sigma(leg: Leg, mkt: MarketState, iv: float | None) -> float:
    """Effective vol for ``leg``: explicit ``iv`` override > ``leg.iv`` > market sigma."""
    if iv is not None:
        return float(iv)
    if leg.iv is not None:
        return float(leg.iv)
    return float(mkt.sigma)


# ---------------------------------------------------------------------------
# Net cost
# ---------------------------------------------------------------------------
def net_cost_entry(legs: Sequence[Leg]) -> float:
    """Net entry cash for ``legs``: debit > 0 / credit < 0.

    Sum of each leg's ``signed_cashflow_entry`` (``qty * entry_price * mult``).
    Pure: independent of the current market. This is the realised cash the payoff
    ladder and every P&L cell subtract once (L3).

    GOLDEN: bull-call 360/460 x8 (entry 91.96 / 53.00) -> 38.96 * 8 * 100 = 31168.
    """
    return float(sum(leg.signed_cashflow_entry() for leg in legs))


def net_cost_fair(legs: Sequence[Leg], market: MarketState) -> float:
    """Theoretical entry-equivalent cash at the current model fair value.

    Same sign convention as ``net_cost_entry`` (long pays = debit > 0, short
    receives = credit < 0), but using the model price now rather than the recorded
    fill: ``sum(fair_price_per_share * mult * qty)``. Used for "what would it cost
    to open this today" framing (e.g. the Monte-Carlo theoretical P&L anchor).
    """
    total = 0.0
    for leg in legs:
        total += _leg_fair_price(leg, market, market.spot, dt_days=0.0, iv=None) * leg.mult * leg.qty
    return float(total)


# ---------------------------------------------------------------------------
# Single-leg model price (the one pricing-core router)
# ---------------------------------------------------------------------------
def _leg_fair_price(leg: Leg, mkt: MarketState, S: float, dt_days: float,
                    iv: float | None) -> float:
    """Per-share model price for one leg at spot ``S`` (time decayed ``dt_days``).

    Routes per design law L1: STOCK -> ``S``; CASH -> ``1.0``; EUROPEAN option ->
    ``bs.price``; AMERICAN option -> ``american.crr_price``; any option under a
    discrete cash-dividend schedule -> ``american.price_discrete_div``.
    """
    if leg.right is Right.STOCK:
        return float(S)
    if leg.right is Right.CASH:
        return 1.0

    T = _leg_T(leg, mkt, dt_days)
    sigma = _leg_sigma(leg, mkt, iv)

    if mkt.div_schedule:
        # Discrete cash dividends: escrowed (European) / CRR (American), q == 0.
        method = "crr" if leg.style is Style.AMERICAN else "escrowed"
        px, _used = _american.price_discrete_div(
            S, leg.strike, T, mkt.r, 0.0, sigma, leg.right, leg.style,
            mkt.div_schedule, method=method,
        )
        return float(px)

    if leg.style is Style.AMERICAN:
        return float(
            _american.crr_price(S, leg.strike, T, mkt.r, mkt.q, sigma,
                                 leg.right, leg.style)
        )
    return float(_bs.price(S, leg.strike, T, mkt.r, mkt.q, sigma, leg.right))


# ---------------------------------------------------------------------------
# Mark-to-model value
# ---------------------------------------------------------------------------
def value_position(legs: Sequence[Leg], S: float, mkt: MarketState,
                   dt_days: float = 0.0, iv: float | None = None) -> dict:
    """Signed mark-to-model dollar value of ``legs`` at spot ``S``.

    Time is decayed by ``dt_days`` calendar days off each leg's expiry; the
    effective vol is the ``iv`` flat override (if given) else each leg's own
    ``leg.iv`` else ``mkt.sigma``. Per leg the per-share model price (L1 router)
    is scaled by ``mult * qty`` (signed). At ``T -> 0`` each option leg's price
    converges to its intrinsic, so this kernel's terminal limit equals
    ``payoff.terminal_payoff`` (L3 single source).

    Returns a dict:
        {
          'position_value': float,          # signed total dollar mark
          'legs': [ {right, strike, qty, mult, price, value}, ... ],
        }
    Consumers read ``['position_value']`` (scenario / charts); the per-leg
    breakdown is available for reporting.
    """
    s = float(S)
    total = 0.0
    leg_rows: list[dict] = []
    for leg in legs:
        px = _leg_fair_price(leg, mkt, s, dt_days, iv)
        val = px * leg.mult * leg.qty  # signed dollar value
        total += val
        leg_rows.append({
            "right": leg.right.value,
            "strike": leg.strike,
            "qty": leg.qty,
            "mult": leg.mult,
            "price": px,
            "value": val,
        })
    return {"position_value": float(total), "legs": leg_rows}


# ---------------------------------------------------------------------------
# Dollar greeks
# ---------------------------------------------------------------------------
def _zero_dollar_greeks() -> dict[str, float]:
    out = {k: 0.0 for k in _FIRST_ORDER_KEYS}
    for k in _HIGHER_ORDER_KEYS:
        out[k] = 0.0
    return out


def dollar_greeks(legs: Sequence[Leg], S: float, mkt: MarketState,
                  dt_days: float = 0.0, iv: float | None = None) -> dict[str, float]:
    """Signed dollar greeks of ``legs`` at spot ``S``, summed across legs.

    Per-leg convention (L5): ``dollar_greek = per_share_greek * leg.mult * leg.qty``
    (already signed by qty). Option legs take the per-share analytic surface from
    ``bs.all_greeks``; a STOCK leg contributes ``delta = qty * mult`` (the
    share-equivalent delta) and zero to every other greek; a CASH leg contributes
    nothing.

    Returns ``dict[str, float]`` keyed: delta, gamma, theta_day, theta_yr, vega
    (per vol pt), rho (per 1%), plus higher-order vanna/vomma/veta/ultima/
    charm_day/speed/color_day/zomma. The book aggregator and scenario grid read
    these via ``.get(key, 0.0)``.
    """
    s = float(S)
    out = _zero_dollar_greeks()
    for leg in legs:
        if leg.right is Right.STOCK:
            out["delta"] += leg.qty * leg.mult  # share-equivalent delta; +1/share
            continue
        if leg.right is Right.CASH:
            continue  # cash has no spot/vol/time sensitivity

        T = _leg_T(leg, mkt, dt_days)
        sigma = _leg_sigma(leg, mkt, iv)
        if T <= 0.0 or sigma <= 0.0:
            # Degenerate: no analytic greek surface. Delta -> intrinsic indicator
            # so the share-equivalent delta is still well-defined; rest zero.
            intrin_delta = _expiry_delta(leg, s)
            out["delta"] += intrin_delta * leg.mult * leg.qty
            continue

        g = _bs.all_greeks(s, leg.strike, T, mkt.r, mkt.q, sigma, leg.right)
        scale = leg.mult * leg.qty  # signed
        out["delta"] += g.delta * scale
        out["gamma"] += g.gamma * scale
        out["theta_day"] += g.theta_day * scale
        out["theta_yr"] += g.theta_yr * scale
        out["vega"] += g.vega * scale
        out["rho"] += g.rho * scale
        for k in _HIGHER_ORDER_KEYS:
            v = getattr(g, k, None)
            if v is not None:
                out[k] += v * scale
    return out


def _expiry_delta(leg: Leg, S: float) -> float:
    """Per-share delta of an option leg at/after expiry (the intrinsic indicator).

    A call is +1 if in the money, a put -1 if in the money, else 0 -- the T->0
    limit of the analytic delta. Keeps share-equivalent delta defined when the
    analytic surface is degenerate (T <= 0)."""
    if leg.right is Right.CALL:
        return 1.0 if S > leg.strike else 0.0
    if leg.right is Right.PUT:
        return -1.0 if S < leg.strike else 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Per-share aggregate greeks & P&L convenience
# ---------------------------------------------------------------------------
def position_greeks(legs: Sequence[Leg], S: float, mkt: MarketState,
                    dt_days: float = 0.0, iv: float | None = None) -> dict[str, float]:
    """Per-SHARE aggregate greeks (no qty/mult scaling) -- signed by qty only.

    Useful when a consumer wants the structure's net per-share sensitivity rather
    than the dollar exposure. Same keys as ``dollar_greeks``; each option leg
    contributes ``per_share_greek * sign(qty)`` (a STOCK leg contributes
    ``sign`` to delta)."""
    s = float(S)
    out = _zero_dollar_greeks()
    for leg in legs:
        sign = leg.sign
        if leg.right is Right.STOCK:
            out["delta"] += sign
            continue
        if leg.right is Right.CASH:
            continue
        T = _leg_T(leg, mkt, dt_days)
        sigma = _leg_sigma(leg, mkt, iv)
        if T <= 0.0 or sigma <= 0.0:
            out["delta"] += _expiry_delta(leg, s) * sign
            continue
        g = _bs.all_greeks(s, leg.strike, T, mkt.r, mkt.q, sigma, leg.right)
        out["delta"] += g.delta * sign
        out["gamma"] += g.gamma * sign
        out["theta_day"] += g.theta_day * sign
        out["theta_yr"] += g.theta_yr * sign
        out["vega"] += g.vega * sign
        out["rho"] += g.rho * sign
        for k in _HIGHER_ORDER_KEYS:
            v = getattr(g, k, None)
            if v is not None:
                out[k] += v * sign
    return out


def pnl_now(legs: Sequence[Leg], S: float, mkt: MarketState,
            dt_days: float = 0.0, iv: float | None = None) -> float:
    """Mark-to-model P&L vs realised entry: ``value_position - net_cost_entry``."""
    val = value_position(legs, S, mkt, dt_days=dt_days, iv=iv)
    return float(val["position_value"]) - net_cost_entry(legs)
