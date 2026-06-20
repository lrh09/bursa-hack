"""Volatility analytics: implied-vol surfaces of the chain + realized-vol estimators.

This module covers the *cross-sectional* and *historical* volatility lenses that
sit on top of the single-quote IV solver in `iv.py`:

  - Per-strike chain IV (picks the OTM wing, tags method/flag).
  - ATM IV (forward-interpolated), term structure, forward vol, skew (RR/BF).
  - Constant-maturity IV by total-variance interpolation.
  - Realized (historical) vol estimators: close-to-close, Parkinson,
    Garman-Klass, Rogers-Satchell, Yang-Zhang; plus a vol cone.

Formula blocks
--------------
Forward price (used everywhere so moneyness is forward-relative):

    F = S * exp((r - q) * T)            moneyness = K / F

Forward (between-tenor) volatility from two ATM IVs (variance is additive):

    fwd_vol(T1,T2) = sqrt( (iv2^2*T2 - iv1^2*T1) / (T2 - T1) )

Constant-maturity IV by total-variance interpolation to `target_days`:

    var(t) = iv(t)^2 * t ,   linear in t ,   iv_cm = sqrt(var(target)/target)

Risk reversal / butterfly (25-delta convention here keyed off wings):

    RR25 = iv(25d call) - iv(25d put)
    BF25 = 0.5*(iv_call + iv_put) - iv_atm

Realized-vol estimators (annualized with factor `ann`, default 252).
For N daily observations:

    close-to-close (zero-mean):  sigma = sqrt( ann/N * sum( r_i^2 ) ),  r_i = ln(C_i/C_{i-1})
    close-to-close (sample)   :  sigma = std(r, ddof=1) * sqrt(ann)
    Parkinson                 :  sigma = sqrt( ann/(4 ln2) * mean( ln(H/L)^2 ) )
    Garman-Klass              :  sigma = sqrt( ann * mean( 0.5 ln(H/L)^2 - (2 ln2 - 1) ln(C/O)^2 ) )
    Rogers-Satchell           :  sigma = sqrt( ann * mean( ln(H/C)ln(H/O) + ln(L/C)ln(L/O) ) )
    Yang-Zhang                :  sigma = sqrt( ann * ( sig_open^2 + k*sig_close^2 + (1-k)*sig_rs^2 ) )
                                 k = 0.34 / (1.34 + (n+1)/(n-1))

Parkinson and Garman-Klass use only the intraday range / close-open and so
*systematically understate* total realized vol when overnight gaps carry a large
share of the move (they cannot see the jump between yesterday's close and
today's open). Rogers-Satchell is drift-robust; Yang-Zhang adds the overnight
term back and is the least biased of the five.

References:
  - Parkinson (1980); Garman & Klass (1980); Rogers & Satchell (1991);
    Yang & Zhang (2000), J. Business.
  - Euan Sinclair, "Volatility Trading" -- estimator bias / vol cones.
"""
from __future__ import annotations

import math

from bursahack.options.iv import implied_vol

__all__ = [
    # implied-vol / chain
    "chain_iv", "atm_iv", "term_structure", "forward_vol", "skew",
    "constant_maturity_iv",
    # realized-vol estimators (canonical home is `realized.py`; mirrored here
    # for the vol lens so the toolkit is usable without that module landing)
    "hv_close_to_close", "hv_parkinson", "hv_garman_klass",
    "hv_rogers_satchell", "hv_yang_zhang", "vol_cone",
]


# ---------------------------------------------------------------------------
# Chain implied vol (cross-sectional)
# ---------------------------------------------------------------------------
def chain_iv(chain_df, S: float, r: float, q: float):
    """Add per-strike implied-vol columns to a chain DataFrame.

    Two input layouts are supported (auto-detected):

    1. **Two-wing chain** -- a row per strike carrying BOTH a call and a put
       price (columns ``call_mid``/``put_mid``, or ``call_last``/``put_last``,
       or ``call_price``/``put_price``). For each strike the *out-of-the-money*
       wing is solved (the call when K >= F, the put when K < F) using that
       wing's own price -- the liquid, well-conditioned side.
    2. **Single-option rows** -- a row per contract carrying ``right``
       (Right or 'C'/'P') and one price (``mid`` preferred, else ``last`` /
       ``price``). Each row's IV is solved from ITS OWN right. ``otm_side_used``
       records whether that contract is the OTM side at its strike.

    Required columns either way: ``strike``, ``T`` (years).
    Adds columns: ``iv``, ``iv_method``, ``iv_flag``, ``otm_side_used``,
    ``moneyness`` (= K / F). Returns the augmented DataFrame (pandas in/out).
    """
    import pandas as pd  # lazy
    from bursahack.options.types import Right

    df = chain_df.copy()

    # Detect a two-wing layout by presence of paired call/put price columns.
    wing_pairs = [("call_mid", "put_mid"), ("call_last", "put_last"),
                  ("call_price", "put_price")]
    two_wing = next(((c, p) for c, p in wing_pairs
                     if c in df.columns and p in df.columns), None)

    def _coerce_right(val) -> Right:
        if isinstance(val, Right):
            return val
        return Right(str(val))

    ivs: list[float] = []
    methods: list[str] = []
    flags: list[str] = []
    sides: list[str] = []
    moneyness: list[float] = []

    for _, row in df.iterrows():
        K = float(row["strike"])
        T = float(row["T"])
        F = S * math.exp((r - q) * T)
        mny = K / F if F > 0 else float("nan")
        moneyness.append(mny)

        otm_side = "call" if K >= F else "put"
        sides.append(otm_side)

        if two_wing is not None:
            call_col, put_col = two_wing
            right = Right.CALL if otm_side == "call" else Right.PUT
            price = row[call_col] if otm_side == "call" else row[put_col]
            price = float(price) if pd.notna(price) else None
        else:
            # Single-option row: solve from the contract's own right.
            right = _coerce_right(row["right"]) if "right" in df.columns else (
                Right.CALL if otm_side == "call" else Right.PUT)
            price = None
            for col in ("mid", "last", "price"):
                if col in df.columns and pd.notna(row.get(col)):
                    price = float(row[col])
                    break

        if price is None or price <= 0:
            ivs.append(float("nan"))
            methods.append("none")
            flags.append("no-price")
            continue

        res = implied_vol(price, S, K, T, r, q, right)
        ivs.append(res.iv)
        methods.append(res.method)
        flags.append(res.flag)

    df["iv"] = ivs
    df["iv_method"] = methods
    df["iv_flag"] = flags
    df["otm_side_used"] = sides
    df["moneyness"] = moneyness
    return df


def atm_iv(chain_iv_df, S: float, r: float, q: float,
           target: str = "forward") -> dict:
    """At-the-money IV per expiry from a chain already carrying an ``iv`` column.

    ``target='forward'`` picks, within each expiry, the strike whose moneyness
    K/F is closest to 1. Returns {expiry: iv}. Rows with a NaN IV are ignored.
    """
    import pandas as pd  # noqa: F401

    out: dict = {}
    if "expiry" not in chain_iv_df.columns:
        # Single-expiry chain: key by the lone T.
        sub = chain_iv_df.dropna(subset=["iv"])
        if len(sub) == 0:
            return out
        sub = sub.assign(_dist=(sub["moneyness"] - 1.0).abs())
        atm_row = sub.loc[sub["_dist"].idxmin()]
        out[float(atm_row["T"])] = float(atm_row["iv"])
        return out

    for expiry, grp in chain_iv_df.groupby("expiry"):
        sub = grp.dropna(subset=["iv"])
        if len(sub) == 0:
            continue
        sub = sub.assign(_dist=(sub["moneyness"] - 1.0).abs())
        atm_row = sub.loc[sub["_dist"].idxmin()]
        out[expiry] = float(atm_row["iv"])
    return out


# ---------------------------------------------------------------------------
# Term structure / forward vol
# ---------------------------------------------------------------------------
def forward_vol(iv1: float, T1: float, iv2: float, T2: float) -> float:
    """Between-tenor forward vol. Returns NaN (caller flags) on negative
    forward variance -- a calendar-arbitrage signal.

        fwd = sqrt( (iv2^2*T2 - iv1^2*T1) / (T2 - T1) )
    """
    if T2 == T1:
        return float("nan")
    fwd_var = (iv2 * iv2 * T2 - iv1 * iv1 * T1) / (T2 - T1)
    if fwd_var < 0.0:
        return float("nan")
    return math.sqrt(fwd_var)


def _tenor_years(key) -> float:
    """Coerce a term-structure key to years. Accepts a float (years), an int
    (days), or anything float-able; ints/strings of magnitude > 5 are treated
    as DAYS (no sane tenor in years exceeds ~5)."""
    val = float(key)
    if val > 5.0:           # interpret as days
        return val / 365.0
    return val


def term_structure(atm_by_expiry: dict) -> dict:
    """Build the ATM term structure from {expiry: iv}.

    Returns {points, front_back_ratios, forward_vol_matrix, calendar_arb_flags}.
      - points: [(T_years, iv), ...] sorted by T.
      - front_back_ratios: iv[i]/iv[i+1] for adjacent tenors.
      - forward_vol_matrix: {(T_i, T_j): fwd_vol} for i < j.
      - calendar_arb_flags: [(T_i, T_j)] where forward variance went negative.
    """
    points = sorted(((_tenor_years(k), float(v)) for k, v in atm_by_expiry.items()),
                    key=lambda p: p[0])
    ratios: list[float] = []
    for i in range(len(points) - 1):
        _, iv_a = points[i]
        _, iv_b = points[i + 1]
        ratios.append(iv_a / iv_b if iv_b != 0 else float("nan"))

    fwd_matrix: dict = {}
    arb_flags: list = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            T1, iv1 = points[i]
            T2, iv2 = points[j]
            fv = forward_vol(iv1, T1, iv2, T2)
            fwd_matrix[(T1, T2)] = fv
            if math.isnan(fv):
                arb_flags.append((T1, T2))
    return {
        "points": points,
        "front_back_ratios": ratios,
        "forward_vol_matrix": fwd_matrix,
        "calendar_arb_flags": arb_flags,
    }


def constant_maturity_iv(atm_by_expiry: dict, target_days: int = 30) -> float:
    """Interpolate (or extrapolate-flat) a constant-maturity IV at `target_days`
    using TOTAL-VARIANCE linear interpolation (var = iv^2 * t).

    Exact hit returns that IV. Below the nearest tenor / above the farthest
    tenor, returns the nearest-tenor IV (flat extrapolation -- never negative
    variance).
    """
    target = target_days / 365.0
    pts = sorted(((_tenor_years(k), float(v)) for k, v in atm_by_expiry.items()),
                 key=lambda p: p[0])
    if not pts:
        return float("nan")
    if len(pts) == 1:
        return pts[0][1]

    # Below / above range -> flat extrapolation.
    if target <= pts[0][0]:
        return pts[0][1]
    if target >= pts[-1][0]:
        return pts[-1][1]

    # Find bracketing tenors.
    for i in range(len(pts) - 1):
        t_lo, iv_lo = pts[i]
        t_hi, iv_hi = pts[i + 1]
        if t_lo <= target <= t_hi:
            if math.isclose(target, t_lo):
                return iv_lo
            if math.isclose(target, t_hi):
                return iv_hi
            var_lo = iv_lo * iv_lo * t_lo
            var_hi = iv_hi * iv_hi * t_hi
            w = (target - t_lo) / (t_hi - t_lo)
            var = var_lo + w * (var_hi - var_lo)
            return math.sqrt(max(var, 0.0) / target)
    return pts[-1][1]


# ---------------------------------------------------------------------------
# Skew
# ---------------------------------------------------------------------------
def skew(chain_iv_expiry, S: float, r: float, q: float, T: float) -> dict:
    """Skew metrics for a single expiry slice (a chain DataFrame for one T that
    already carries ``iv`` and ``moneyness`` columns).

    Returns {table, RR25, BF25, skew_slope, put_wing, call_wing, atm_iv}:
      - atm_iv     : IV at the strike nearest the forward.
      - put_wing   : IV at moneyness ~ 0.90 (or nearest below ATM).
      - call_wing  : IV at moneyness ~ 1.10 (or nearest above ATM).
      - RR25       : call_wing - put_wing (positive = call skew / smirk up).
      - BF25       : 0.5*(call_wing + put_wing) - atm_iv (smile convexity).
      - skew_slope : d(iv)/d(logmoneyness) via an OLS fit across the slice.
      - table      : [(moneyness, iv), ...] sorted by moneyness.
    """
    import pandas as pd  # noqa: F401

    sub = chain_iv_expiry.dropna(subset=["iv"]).copy()
    sub = sub.sort_values("moneyness")
    mny = sub["moneyness"].to_numpy()
    ivv = sub["iv"].to_numpy()
    table = list(zip(mny.tolist(), ivv.tolist()))

    if len(sub) == 0:
        return {"table": [], "RR25": float("nan"), "BF25": float("nan"),
                "skew_slope": float("nan"), "put_wing": float("nan"),
                "call_wing": float("nan"), "atm_iv": float("nan")}

    def _nearest_iv(target_mny: float) -> float:
        idx = min(range(len(mny)), key=lambda k: abs(mny[k] - target_mny))
        return float(ivv[idx])

    atm = _nearest_iv(1.0)
    put_wing = _nearest_iv(0.90)
    call_wing = _nearest_iv(1.10)
    rr = call_wing - put_wing
    bf = 0.5 * (call_wing + put_wing) - atm

    # OLS slope of iv vs ln(moneyness).
    logm = [math.log(m) for m in mny if m > 0]
    if len(logm) >= 2:
        xs = logm
        ys = [ivv[i] for i, m in enumerate(mny) if m > 0]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        slope = num / den if den != 0 else float("nan")
    else:
        slope = float("nan")

    return {
        "table": table,
        "RR25": rr,
        "BF25": bf,
        "skew_slope": slope,
        "put_wing": put_wing,
        "call_wing": call_wing,
        "atm_iv": atm,
    }


# ---------------------------------------------------------------------------
# Realized-vol estimators
# (Canonical home is `realized.py`; mirrored here so the vol lens is usable.)
# ---------------------------------------------------------------------------
def _as_np(x):
    import numpy as np
    return np.asarray(x, dtype=float)


def hv_close_to_close(closes, window: int, ann: int = 252,
                      zero_mean: bool = True, ewma_lambda: float | None = None):
    """Annualized close-to-close historical vol over a trailing `window`.

    zero_mean=True uses sqrt(ann/N * sum r^2); False uses sample std (ddof=1).
    If `ewma_lambda` is given, an exponentially-weighted variance is used
    (RiskMetrics style) instead of an equal-weight window. Returns the latest
    estimate (scalar).
    """
    import numpy as np

    c = _as_np(closes)
    rets = np.diff(np.log(c))
    if len(rets) < 1:
        return float("nan")

    if ewma_lambda is not None:
        lam = ewma_lambda
        # Weight most-recent return heaviest; normalize weights.
        n = len(rets)
        weights = np.array([(1 - lam) * lam ** (n - 1 - i) for i in range(n)])
        weights /= weights.sum()
        var = float(np.sum(weights * rets ** 2))
        return math.sqrt(var * ann)

    r = rets[-window:] if window < len(rets) else rets
    if len(r) < 1:
        return float("nan")
    if zero_mean:
        var = float(np.mean(r ** 2))
    else:
        if len(r) < 2:
            return float("nan")
        var = float(np.var(r, ddof=1))
    return math.sqrt(var * ann)


def hv_parkinson(high, low, window, ann: int = 252):
    """Parkinson range estimator. Understates vol when overnight gaps dominate."""
    import numpy as np

    h = _as_np(high)
    l = _as_np(low)
    hl = np.log(h / l) ** 2
    hl = hl[-window:] if window < len(hl) else hl
    if len(hl) < 1:
        return float("nan")
    var = float(np.mean(hl)) / (4.0 * math.log(2.0))
    return math.sqrt(var * ann)


def hv_garman_klass(o, h, l, c, window, ann: int = 252):
    """Garman-Klass OHLC estimator. Also gap-blind (close-open + range only)."""
    import numpy as np

    O, H, L, C = _as_np(o), _as_np(h), _as_np(l), _as_np(c)
    term = 0.5 * np.log(H / L) ** 2 - (2.0 * math.log(2.0) - 1.0) * np.log(C / O) ** 2
    term = term[-window:] if window < len(term) else term
    if len(term) < 1:
        return float("nan")
    var = float(np.mean(term))
    return math.sqrt(max(var, 0.0) * ann)


def hv_rogers_satchell(o, h, l, c, window, ann: int = 252):
    """Rogers-Satchell estimator -- drift-independent (handles trending names)."""
    import numpy as np

    O, H, L, C = _as_np(o), _as_np(h), _as_np(l), _as_np(c)
    term = np.log(H / C) * np.log(H / O) + np.log(L / C) * np.log(L / O)
    term = term[-window:] if window < len(term) else term
    if len(term) < 1:
        return float("nan")
    var = float(np.mean(term))
    return math.sqrt(max(var, 0.0) * ann)


def hv_yang_zhang(o, h, l, c, window, ann: int = 252) -> tuple[float, dict]:
    """Yang-Zhang estimator: overnight + open-close + Rogers-Satchell drift term.

    Least biased of the five (handles both drift and overnight gaps). Returns
    (annualized_vol, components) where components has the open/close/rs variances
    and k. [Contract tags this DEFERRED-v2 in `realized.py`; provided here.]
    """
    import numpy as np

    O, H, L, C = _as_np(o), _as_np(h), _as_np(l), _as_np(c)
    O, H, L, C = O[-(window + 1):], H[-(window + 1):], L[-(window + 1):], C[-(window + 1):]
    if len(C) < 3:
        return float("nan"), {"sigma_open2": float("nan"),
                              "sigma_close2": float("nan"),
                              "sigma_rs2": float("nan"), "k": float("nan")}

    o_ret = np.log(O[1:] / C[:-1])            # overnight
    c_ret = np.log(C[1:] / O[1:])             # open-to-close
    n = len(o_ret)
    sigma_open2 = float(np.var(o_ret, ddof=1))
    sigma_close2 = float(np.var(c_ret, ddof=1))
    rs = (np.log(H[1:] / C[1:]) * np.log(H[1:] / O[1:])
          + np.log(L[1:] / C[1:]) * np.log(L[1:] / O[1:]))
    sigma_rs2 = float(np.mean(rs))
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = sigma_open2 + k * sigma_close2 + (1.0 - k) * sigma_rs2
    components = {"sigma_open2": sigma_open2, "sigma_close2": sigma_close2,
                 "sigma_rs2": sigma_rs2, "k": k}
    return math.sqrt(max(var, 0.0) * ann), components


def vol_cone(ohlc, windows, estimator: str = "rogers_satchell",
             current_iv_by_tenor: dict | None = None) -> dict:
    """Realized-vol cone: rolling min/median/max of an estimator across tenors.

    `ohlc` is a mapping/DataFrame with keys/columns o,h,l,c (close-to-close uses
    only c). For each window length, the estimator is rolled across the series
    and the {min, p25, median, p75, max, current} captured.

    If `current_iv_by_tenor` ({window: iv}) is supplied, each tenor reports
    `iv_vs_realized_pctile` -- where the live IV sits inside that window's
    realized distribution (a rich/cheap-vol read). Returns
    {by_window:{w:{...}}, estimator, windows}. [Contract DEFERRED-v2; provided.]
    """
    import numpy as np

    def _col(name):
        if hasattr(ohlc, "__getitem__"):
            try:
                return _as_np(ohlc[name])
            except (KeyError, TypeError):
                pass
        return _as_np(getattr(ohlc, name))

    c = _col("c")
    has_ohlc = estimator != "close_to_close"
    if has_ohlc:
        o = _col("o")
        h = _col("h")
        l = _col("l")

    def _est(sl_o, sl_h, sl_l, sl_c, w):
        if estimator == "close_to_close":
            return hv_close_to_close(sl_c, w)
        if estimator == "parkinson":
            return hv_parkinson(sl_h, sl_l, w)
        if estimator == "garman_klass":
            return hv_garman_klass(sl_o, sl_h, sl_l, sl_c, w)
        if estimator == "yang_zhang":
            return hv_yang_zhang(sl_o, sl_h, sl_l, sl_c, w)[0]
        return hv_rogers_satchell(sl_o, sl_h, sl_l, sl_c, w)

    by_window: dict = {}
    n = len(c)
    for w in windows:
        samples: list[float] = []
        # Roll the window across the full series (need w+1 closes per estimate).
        for end in range(w + 1, n + 1):
            start = end - (w + 1)
            sl_c = c[start:end]
            if has_ohlc:
                sl_o, sl_h, sl_l = o[start:end], h[start:end], l[start:end]
            else:
                sl_o = sl_h = sl_l = None
            val = _est(sl_o, sl_h, sl_l, sl_c, w)
            if not math.isnan(val):
                samples.append(val)
        if not samples:
            by_window[w] = {"min": float("nan"), "p25": float("nan"),
                            "median": float("nan"), "p75": float("nan"),
                            "max": float("nan"), "current": float("nan"),
                            "n": 0}
            continue
        arr = np.array(samples)
        entry = {
            "min": float(arr.min()),
            "p25": float(np.percentile(arr, 25)),
            "median": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "max": float(arr.max()),
            "current": float(arr[-1]),
            "n": len(arr),
        }
        if current_iv_by_tenor and w in current_iv_by_tenor:
            iv_now = current_iv_by_tenor[w]
            pctile = float((arr < iv_now).mean() * 100.0)
            entry["live_iv"] = iv_now
            entry["iv_vs_realized_pctile"] = pctile
        by_window[w] = entry

    return {"by_window": by_window, "estimator": estimator,
            "windows": list(windows)}
