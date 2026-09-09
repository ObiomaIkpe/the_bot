"""
signal_detector.py -- reimplementation of the reference package's
`scalp_common.detect_signals()` (liquidity sweep -> rapid market-
structure-shift -> fair-value-gap -> equilibrium-filter detection).

A GENUINE FINDING, confirmed against the reference's actual source
(not its comments) -- READ BEFORE CHANGING THIS FILE'S STRUCTURE:

An earlier version of this module modeled the sweep-consumption rule
as "the pool is consumed the INSTANT a sweep condition triggers,
whether or not an MSS is ever found" -- following the reference's own
inline comment, `recent_sl_price = None  # pool consumed either way
(swept)`. That version passed extensive synthetic-data testing but
FAILED against real HistData. Tracing the failure down to the
reference's actual source with `cat -A` (not just reading it) showed
the comment is misleading: `recent_sl_price = None` is indented INSIDE
`if mss_idx is not None:` -- both for the bearish and bullish blocks,
confirmed identically in both. The pool is only actually consumed once
an MSS is genuinely found within the MSS_WINDOW; a sweep attempt that
fails to find one leaves the pool untouched, free to be swept again by
a LATER bar. This also explains a duplicate-looking signal noticed
earlier in the reference's own raw output -- two different bars
attempting (and one eventually succeeding at) a sweep of the exact
same still-unconsumed pool value.

The consequence for streaming, same shape as position_manager.py's own
finding: because the reference resolves each bar's ENTIRE sweep
attempt (up to a 15-bar-deep MSS search) via instant lookahead before
its outer loop even advances to the next bar, there is never more than
one attempt "in flight" against a given pool value at once, from the
reference's own perspective -- a later bar only gets to try because
the earlier bar's own attempt has ALREADY been fully resolved (found
MSS or not) by the time the outer loop reaches it. A genuinely live
process can't replicate that -- it would need to already know, at bar
i+1, whether bar i's own attempt (which needs up to 15 MORE bars to
resolve) is going to succeed. Not solvable by a cleverer streaming
design; flagged here the same way as the breaker's own forward-looking
dependency, for the same later Phase 2 decision.

WHAT THIS MODULE ACTUALLY DOES: reproduces the reference's exact
sequential structure -- for each bar, check the sweep condition; if
met, resolve it FULLY via a bounded (MSS_WINDOW-bar) local lookahead
before considering the next bar, exactly matching the reference's own
order of operations. Bounded lookahead is legitimate here because
Phase 1's job is reproducing already-known history, not real-time
prediction -- same discipline as position_manager.py.
"""
PIP = 0.0001
STRUCTURE_LOOKBACK = 10
MSS_WINDOW = 15
FILL_EXPIRY = 30
FRACTAL_WING = 2


def _find_fractal_swings(high, low, wing: int = FRACTAL_WING):
    n = len(high)
    swing_high = [False] * n
    swing_low = [False] * n
    for i in range(wing, n - wing):
        window_h = high[i - wing:i + wing + 1]
        if high[i] == max(window_h) and window_h.count(high[i]) == 1:
            swing_high[i] = True
        window_l = low[i - wing:i + wing + 1]
        if low[i] == min(window_l) and window_l.count(low[i]) == 1:
            swing_low[i] = True
    return swing_high, swing_low


def _find_fvg_bearish(high, low, sweep_idx: int, mss_idx: int):
    for k in range(mss_idx, sweep_idx + 1, -1):
        if low[k - 2] > high[k]:
            return k, low[k - 2], high[k], high[k - 1], low[k - 1]  # k, fvg_top, fvg_bot, frame_hi, frame_lo
    return None


def _find_fvg_bullish(high, low, sweep_idx: int, mss_idx: int):
    for k in range(mss_idx, sweep_idx + 1, -1):
        if high[k - 2] < low[k]:
            return k, low[k], high[k - 2], high[k - 1], low[k - 1]  # k, fvg_top, fvg_bot, frame_hi, frame_lo
    return None


def detect_signals(high, low, close) -> list[dict]:
    """`high`/`low`/`close`: full bar series (list or array-like,
    indexable). Bounded-lookahead reproduction of the reference's
    detect_signals() -- see this module's own docstring for why a
    persistent-pending-watch streaming design (the earlier, wrong
    version of this file) doesn't actually match the reference's real
    consumption timing."""
    # Accept numpy arrays or plain lists -- .count()-based uniqueness
    # checks below need plain lists either way.
    high = list(high)
    low = list(low)
    close = list(close)
    n = len(high)
    swing_high, swing_low = _find_fractal_swings(high, low)

    signals: list[dict] = []
    recent_sh_price = recent_sh_idx = None
    recent_sl_price = recent_sl_idx = None
    w = FRACTAL_WING

    for i in range(n):
        if i >= w:
            conf_i = i - w
            if swing_high[conf_i]:
                recent_sh_price = high[conf_i]
                recent_sh_idx = conf_i
            if swing_low[conf_i]:
                recent_sl_price = low[conf_i]
                recent_sl_idx = conf_i

        # --- Bearish candidate: sweep of recent swing high ---
        if recent_sh_price is not None and high[i] > recent_sh_price and close[i] < recent_sh_price:
            structure_lo_start = max(0, i - STRUCTURE_LOOKBACK)
            structure_low = min(low[structure_lo_start:i + 1])
            sweep_idx = i
            mss_idx = None
            for j in range(i + 1, min(n, i + 1 + MSS_WINDOW)):
                if close[j] < structure_low:
                    mss_idx = j
                    break
            if mss_idx is not None:
                fvg = _find_fvg_bearish(high, low, sweep_idx, mss_idx)
                if fvg is not None:
                    _, fvg_top, fvg_bot, frame_hi, frame_lo = fvg
                    leg_range = high[sweep_idx] - low[mss_idx]
                    if leg_range > 0:
                        fvg_mid = (fvg_top + fvg_bot) / 2
                        eq = low[mss_idx] + leg_range * 0.5
                        if fvg_mid >= eq:  # aggressive/expensive half for bearish
                            signals.append(dict(
                                direction="short", sweep_idx=sweep_idx, mss_idx=mss_idx,
                                fvg_near=fvg_top, fvg_far=fvg_bot,
                                frame_high=frame_hi, frame_low=frame_lo,
                                leg_extreme=low[mss_idx], signal_idx=mss_idx,
                            ))
                # Pool consumed ONLY when an MSS was actually found -- see
                # module docstring. A failed attempt leaves it untouched.
                recent_sh_price = None

        # --- Bullish candidate: sweep of recent swing low ---
        if recent_sl_price is not None and low[i] < recent_sl_price and close[i] > recent_sl_price:
            structure_hi_start = max(0, i - STRUCTURE_LOOKBACK)
            structure_high = max(high[structure_hi_start:i + 1])
            sweep_idx = i
            mss_idx = None
            for j in range(i + 1, min(n, i + 1 + MSS_WINDOW)):
                if close[j] > structure_high:
                    mss_idx = j
                    break
            if mss_idx is not None:
                fvg = _find_fvg_bullish(high, low, sweep_idx, mss_idx)
                if fvg is not None:
                    _, fvg_top, fvg_bot, frame_hi, frame_lo = fvg
                    leg_range = high[mss_idx] - low[sweep_idx]
                    if leg_range > 0:
                        fvg_mid = (fvg_top + fvg_bot) / 2
                        eq = low[sweep_idx] + leg_range * 0.5
                        if fvg_mid <= eq:  # aggressive/cheap half for bullish
                            signals.append(dict(
                                direction="long", sweep_idx=sweep_idx, mss_idx=mss_idx,
                                fvg_near=fvg_bot, fvg_far=fvg_top,
                                frame_high=frame_hi, frame_low=frame_lo,
                                leg_extreme=high[mss_idx], signal_idx=mss_idx,
                            ))
                recent_sl_price = None

    return signals
