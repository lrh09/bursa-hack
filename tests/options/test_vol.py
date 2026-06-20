"""Lock the volatility analytics: chain IV, term structure, forward vol, skew,
constant-maturity IV, and the realized-vol estimators.

Realized-vol estimators are validated on a synthetic GBM series with a KNOWN
annualized sigma: close-to-close must recover it tightly; the range estimators
recover it within a looser band (intraday discretization bias); and a gap
injection makes Parkinson / Garman-Klass UNDERSTATE (they are blind to the
overnight jump). Math shown in comments.
"""
from __future__ import annotations

import math

import numpy as np

from bursahack.options.vol import (
    atm_iv,
    chain_iv,
    constant_maturity_iv,
    forward_vol,
    hv_close_to_close,
    hv_garman_klass,
    hv_parkinson,
    hv_rogers_satchell,
    hv_yang_zhang,
    skew,
    term_structure,
    vol_cone,
)


# ---------------------------------------------------------------------------
# Forward vol: variance is additive in time
# ---------------------------------------------------------------------------
def test_forward_vol_additive_variance():
    # iv1=0.40 @ T1=0.25, iv2=0.45 @ T2=0.50:
    # fwd = sqrt((0.45^2*0.5 - 0.40^2*0.25)/0.25) = sqrt((0.10125-0.04)/0.25)
    #     = sqrt(0.245) = 0.494975
    fv = forward_vol(0.40, 0.25, 0.45, 0.50)
    assert math.isclose(fv, math.sqrt(0.245), abs_tol=1e-6)


def test_forward_vol_negative_variance_returns_nan():
    # iv falls so fast that forward variance goes negative -> NaN (calendar arb).
    fv = forward_vol(0.60, 0.25, 0.30, 0.50)  # 0.30^2*0.5 - 0.60^2*0.25 < 0
    assert math.isnan(fv)


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------
def test_term_structure_points_sorted_and_ratios():
    atm = {30: 0.50, 7: 0.55, 60: 0.45}  # days
    ts = term_structure(atm)
    Ts = [p[0] for p in ts["points"]]
    assert Ts == sorted(Ts)
    # Backwardation: front (7d) > back (60d).
    assert ts["points"][0][1] > ts["points"][-1][1]
    # forward_vol_matrix keyed by (T_i, T_j) for i<j.
    assert len(ts["forward_vol_matrix"]) == 3  # C(3,2)


def test_term_structure_flags_calendar_arb():
    # Steeply inverted -> a forward variance goes negative -> flagged.
    atm = {30: 0.60, 90: 0.30}
    ts = term_structure(atm)
    assert len(ts["calendar_arb_flags"]) >= 1


# ---------------------------------------------------------------------------
# Constant-maturity IV (total-variance interpolation)
# ---------------------------------------------------------------------------
def test_constant_maturity_exact_hit():
    atm = {7: 0.55, 30: 0.45, 60: 0.42}
    assert math.isclose(constant_maturity_iv(atm, 30), 0.45, abs_tol=1e-9)


def test_constant_maturity_interpolation():
    # Interp to 20d between 7d(.55) and 30d(.45) in total-variance space:
    # var_lo = .55^2*(7/365)=.005801; var_hi=.45^2*(30/365)=.016644
    # w = (20-7)/(30-7) = 0.5652; var = .005801 + w*(.016644-.005801)=.011929
    # iv = sqrt(var / (20/365)) = sqrt(.011929/.054795) = 0.46657
    cm = constant_maturity_iv({7: 0.55, 30: 0.45}, 20)
    assert math.isclose(cm, 0.4666, abs_tol=1e-3)


def test_constant_maturity_flat_extrapolation():
    atm = {30: 0.45, 60: 0.42}
    # Below range -> nearest tenor.
    assert math.isclose(constant_maturity_iv(atm, 7), 0.45, abs_tol=1e-9)
    # Above range -> farthest tenor.
    assert math.isclose(constant_maturity_iv(atm, 120), 0.42, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Chain IV: recover a flat input vol from priced strikes (round-trip)
# ---------------------------------------------------------------------------
def test_chain_iv_single_option_rows_recover_flat_vol():
    # MS_A flat sigma=0.46. Price three calls forward, then chain_iv must
    # recover 0.46 from each (single-option-row layout).
    import pandas as pd
    from bursahack.options.bsm import price
    from bursahack.options.types import Right
    S = 389.80
    rows = []
    for K in (360.0, 400.0, 460.0):
        rows.append({"strike": K, "right": "C", "T": 1.0,
                     "mid": price(S, K, 1.0, 0.045, 0.0, 0.46, Right.CALL)})
    df = chain_iv(pd.DataFrame(rows), S, r=0.045, q=0.0)
    assert all(math.isclose(v, 0.46, abs_tol=1e-3) for v in df["iv"])
    assert (df["iv_flag"] == "ok").all()
    # moneyness column present and ordered.
    assert list(df["moneyness"]) == sorted(df["moneyness"])


def test_chain_iv_two_wing_picks_otm_side():
    # Two-wing layout: each strike has BOTH a call and put price at sigma=0.46.
    # chain_iv must pick the OTM wing per strike and recover 0.46.
    import pandas as pd
    from bursahack.options.bsm import price
    from bursahack.options.types import Right
    S = 389.80
    F = S * math.exp(0.045)  # forward ~ 407.74
    rows = []
    for K in (360.0, 410.0, 460.0):
        rows.append({
            "strike": K, "T": 1.0,
            "call_mid": price(S, K, 1.0, 0.045, 0.0, 0.46, Right.CALL),
            "put_mid": price(S, K, 1.0, 0.045, 0.0, 0.46, Right.PUT),
        })
    df = chain_iv(pd.DataFrame(rows), S, r=0.045, q=0.0)
    assert all(math.isclose(v, 0.46, abs_tol=1e-3) for v in df["iv"])
    # 360 < F -> put wing; 460 > F -> call wing.
    by_K = dict(zip(df["strike"], df["otm_side_used"]))
    assert by_K[360.0] == "put"
    assert by_K[460.0] == "call"


def test_atm_iv_selects_strike_nearest_forward():
    import pandas as pd
    from bursahack.options.bsm import price
    from bursahack.options.types import Right
    S = 389.80
    rows = []
    for K in (360.0, 410.0, 460.0):
        rows.append({"strike": K, "right": "C", "T": 1.0,
                     "mid": price(S, K, 1.0, 0.045, 0.0, 0.46, Right.CALL)})
    df = chain_iv(pd.DataFrame(rows), S, r=0.045, q=0.0)
    atm = atm_iv(df, S, r=0.045, q=0.0)
    # One expiry (T=1.0); ATM IV ~ 0.46.
    assert math.isclose(list(atm.values())[0], 0.46, abs_tol=1e-3)


# ---------------------------------------------------------------------------
# Skew (RR / BF on a constructed smile slice)
# ---------------------------------------------------------------------------
def test_skew_rr_bf_on_put_smirk():
    import pandas as pd
    # Equity put-smirk: puts (low moneyness) richer than calls.
    df = pd.DataFrame({
        "moneyness": [0.90, 1.00, 1.10],
        "iv": [0.50, 0.45, 0.42],
        "strike": [360.0, 400.0, 440.0],
        "T": [0.25, 0.25, 0.25],
    })
    s = skew(df, S=400.0, r=0.045, q=0.0, T=0.25)
    assert math.isclose(s["atm_iv"], 0.45, abs_tol=1e-9)
    assert math.isclose(s["put_wing"], 0.50, abs_tol=1e-9)
    assert math.isclose(s["call_wing"], 0.42, abs_tol=1e-9)
    # RR25 = call - put = 0.42 - 0.50 = -0.08 (negative -> put skew).
    assert math.isclose(s["RR25"], -0.08, abs_tol=1e-9)
    # BF25 = 0.5*(0.42+0.50) - 0.45 = 0.46 - 0.45 = 0.01 (smile convexity).
    assert math.isclose(s["BF25"], 0.01, abs_tol=1e-9)
    # Put skew -> downward slope of iv vs ln(moneyness).
    assert s["skew_slope"] < 0.0


# ---------------------------------------------------------------------------
# Realized-vol estimators on a synthetic GBM with a KNOWN sigma
# ---------------------------------------------------------------------------
def _make_gbm(true_sigma=0.40, n=5000, ann=252, seed=42):
    """Daily GBM close series with the given annualized sigma."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / ann
    logret = rng.normal(-0.5 * true_sigma ** 2 * dt, true_sigma * math.sqrt(dt), n)
    close = 100.0 * np.exp(np.cumsum(logret))
    return np.concatenate([[100.0], close])


def _make_ohlc(true_sigma=0.40, n=5000, ann=252, seed=7, gap_sigma=0.0):
    """Daily OHLC with an intraday sub-path; optional overnight gap injection."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / ann
    steps = 50
    O, H, L, C = [], [], [], []
    prev_close = 100.0
    for _ in range(n):
        # Overnight gap (open != prev close) when gap_sigma > 0.
        gap = rng.normal(0.0, gap_sigma * math.sqrt(dt)) if gap_sigma > 0 else 0.0
        o = prev_close * math.exp(gap)
        sub = rng.normal(-0.5 * true_sigma ** 2 * (dt / steps),
                         true_sigma * math.sqrt(dt / steps), steps)
        path = o * np.exp(np.cumsum(sub))
        h = max(path.max(), o)
        l = min(path.min(), o)
        c = path[-1]
        O.append(o); H.append(h); L.append(l); C.append(c)
        prev_close = c
    return map(np.array, (O, H, L, C))


def test_close_to_close_recovers_true_sigma():
    close = _make_gbm(true_sigma=0.40, n=5000)
    cc = hv_close_to_close(close, window=5000, zero_mean=True)
    # Tight: close-to-close is the unbiased estimator on a no-gap GBM.
    assert math.isclose(cc, 0.40, abs_tol=0.02)


def test_close_to_close_sample_variant():
    close = _make_gbm(true_sigma=0.40, n=5000)
    cc = hv_close_to_close(close, window=5000, zero_mean=False)
    assert math.isclose(cc, 0.40, abs_tol=0.02)


def test_range_estimators_in_band():
    O, H, L, C = _make_ohlc(true_sigma=0.40, n=5000)
    park = hv_parkinson(H, L, window=5000)
    gk = hv_garman_klass(O, H, L, C, window=5000)
    rs = hv_rogers_satchell(O, H, L, C, window=5000)
    # 50-step intraday discretization understates the true continuous range, so
    # these land somewhat below 0.40 but in a sane band, and all positive.
    for est in (park, gk, rs):
        assert 0.30 < est < 0.45


def test_yang_zhang_recovers_and_components():
    O, H, L, C = _make_ohlc(true_sigma=0.40, n=5000, gap_sigma=0.0)
    yz, comp = hv_yang_zhang(O, H, L, C, window=5000)
    assert 0.30 < yz < 0.45
    assert set(comp) == {"sigma_open2", "sigma_close2", "sigma_rs2", "k"}
    assert 0.0 <= comp["k"] <= 1.0


def test_gap_injection_makes_parkinson_gk_understate_vs_close_to_close():
    # Inject a large overnight gap component. Close-to-close SEES the gap (it is
    # part of consecutive closes); Parkinson/GK are intraday-only and miss it,
    # so they understate relative to close-to-close.
    O, H, L, C = _make_ohlc(true_sigma=0.30, n=4000, gap_sigma=0.40, seed=11)
    cc = hv_close_to_close(C, window=4000, zero_mean=True)
    park = hv_parkinson(H, L, window=4000)
    gk = hv_garman_klass(O, H, L, C, window=4000)
    # The gap-blind estimators sit well below the total (gap-inclusive) vol.
    assert park < cc
    assert gk < cc


def test_ewma_variant_runs_and_is_positive():
    close = _make_gbm(true_sigma=0.40, n=2000)
    ew = hv_close_to_close(close, window=2000, ewma_lambda=0.94)
    assert 0.20 < ew < 0.60


# ---------------------------------------------------------------------------
# Vol cone
# ---------------------------------------------------------------------------
def test_vol_cone_orders_min_le_median_le_max():
    O, H, L, C = _make_ohlc(true_sigma=0.40, n=2000)
    cone = vol_cone({"o": O, "h": H, "l": L, "c": C},
                    windows=[20, 60], estimator="rogers_satchell")
    for w in (20, 60):
        e = cone["by_window"][w]
        assert e["min"] <= e["median"] <= e["max"]
        assert e["n"] > 0


def test_vol_cone_iv_percentile_when_live_iv_given():
    O, H, L, C = _make_ohlc(true_sigma=0.40, n=2000)
    cone = vol_cone({"o": O, "h": H, "l": L, "c": C}, windows=[20],
                    estimator="close_to_close",
                    current_iv_by_tenor={20: 1.0})  # absurdly high IV
    # An IV of 100% sits above every realized 40%-vol sample -> ~100th pctile.
    assert cone["by_window"][20]["iv_vs_realized_pctile"] > 90.0
