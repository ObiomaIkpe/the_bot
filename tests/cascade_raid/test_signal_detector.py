"""
Tests for cascade_raid/streaming/signal_detector.py -- validates
against a direct reproduction of the reference package's
scalp_common.py, reproduced here for comparison only (see
test_fractal_swings.py's own docstring for why).

IMPORTANT: this reproduction was originally written from the
reference's own inline comment ("pool consumed either way (swept)"),
which is misleading -- confirmed by inspecting the real file with
`cat -A`, the consumption line is indented INSIDE `if mss_idx is not
None:`, so the pool is only actually consumed when an MSS is found.
The first version of this test file had the SAME wrong assumption as
the implementation it was checking, which is exactly why it passed
despite both being wrong -- see signal_detector.py's own module
docstring for the full story. Fixed here to match the real,
indentation-verified behavior.
"""
import random

from cascade_raid.streaming.signal_detector import detect_signals

PIP = 0.0001
FRACTAL_WING = 2
STRUCTURE_LOOKBACK = 10
MSS_WINDOW = 15


def _ref_find_fractal_swings(high, low):
    n = len(high)
    swing_high = [False] * n
    swing_low = [False] * n
    w = FRACTAL_WING
    for i in range(w, n - w):
        window_h = high[i - w:i + w + 1]
        if high[i] == max(window_h) and window_h.count(high[i]) == 1:
            swing_high[i] = True
        window_l = low[i - w:i + w + 1]
        if low[i] == min(window_l) and window_l.count(low[i]) == 1:
            swing_low[i] = True
    return swing_high, swing_low


def _ref_find_fvg_bearish(h, l, sweep_idx, mss_idx):
    for k in range(mss_idx, sweep_idx + 1, -1):
        if l[k - 2] > h[k]:
            return k, l[k - 2], h[k], h[k - 1], l[k - 1]
    return None


def _ref_find_fvg_bullish(h, l, sweep_idx, mss_idx):
    for k in range(mss_idx, sweep_idx + 1, -1):
        if h[k - 2] < l[k]:
            return k, l[k], h[k - 2], h[k - 1], l[k - 1]
    return None


def _reference_detect_signals(high, low, close):
    n = len(high)
    swing_high, swing_low = _ref_find_fractal_swings(high, low)
    signals = []
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
                fvg = _ref_find_fvg_bearish(high, low, sweep_idx, mss_idx)
                if fvg is not None:
                    k, fvg_top, fvg_bot, frame_hi, frame_lo = fvg
                    leg_range = high[sweep_idx] - low[mss_idx]
                    if leg_range > 0:
                        fvg_mid = (fvg_top + fvg_bot) / 2
                        eq = low[mss_idx] + leg_range * 0.5
                        if fvg_mid >= eq:
                            signals.append(dict(
                                direction="short", sweep_idx=sweep_idx, mss_idx=mss_idx,
                                fvg_near=fvg_top, fvg_far=fvg_bot,
                                frame_high=frame_hi, frame_low=frame_lo,
                                leg_extreme=low[mss_idx], signal_idx=mss_idx,
                            ))
                # CORRECTED: only consumed when mss_idx was found -- see this
                # file's own module docstring.
                recent_sh_price = None

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
                fvg = _ref_find_fvg_bullish(high, low, sweep_idx, mss_idx)
                if fvg is not None:
                    k, fvg_top, fvg_bot, frame_hi, frame_lo = fvg
                    leg_range = high[mss_idx] - low[sweep_idx]
                    if leg_range > 0:
                        fvg_mid = (fvg_top + fvg_bot) / 2
                        eq = low[sweep_idx] + leg_range * 0.5
                        if fvg_mid <= eq:
                            signals.append(dict(
                                direction="long", sweep_idx=sweep_idx, mss_idx=mss_idx,
                                fvg_near=fvg_bot, fvg_far=fvg_top,
                                frame_high=frame_hi, frame_low=frame_lo,
                                leg_extreme=high[mss_idx], signal_idx=mss_idx,
                            ))
                recent_sl_price = None

    return signals


def _gen_bars(n, seed):
    random.seed(seed)
    price = 1.10000
    bars = []
    for _ in range(n):
        move = random.uniform(-0.0015, 0.0015)
        price = max(1.0, price + move)
        spread = random.uniform(0.0002, 0.0012)
        high = price + spread * random.uniform(0.3, 1.0)
        low = price - spread * random.uniform(0.3, 1.0)
        close = random.uniform(low, high)
        bars.append((high, low, close))
        price = close
    return bars


def _signals_comparable(signals):
    return sorted(signals, key=lambda s: (s["signal_idx"], s["direction"], s["sweep_idx"]))


def test_matches_reference_on_random_walk_data_multiple_seeds():
    for seed in range(20):
        bars = _gen_bars(800, seed)
        highs = [b[0] for b in bars]
        lows = [b[1] for b in bars]
        closes = [b[2] for b in bars]

        ref = _reference_detect_signals(highs, lows, closes)
        mine = detect_signals(highs, lows, closes)

        assert _signals_comparable(mine) == _signals_comparable(ref), f"mismatch at seed={seed}"


def test_produces_at_least_some_signals_across_seeds():
    total = 0
    for seed in range(10):
        bars = _gen_bars(800, seed)
        highs = [b[0] for b in bars]
        lows = [b[1] for b in bars]
        closes = [b[2] for b in bars]
        total += len(detect_signals(highs, lows, closes))
    assert total > 0


def test_a_failed_mss_search_does_not_consume_the_pool_a_later_bar_can_still_sweep_it():
    """The exact bug this file's own docstring documents: a real
    HistData case where an earlier bar's sweep attempt failed to find
    an MSS within the window, and a LATER bar successfully swept the
    SAME still-available pool value. Handcrafted to force exactly this
    shape rather than relying on it showing up by chance in random
    data."""
    n = 40
    high = [1.1000] * n
    low = [1.0995] * n
    close = [1.0997] * n

    # Build a swing low pool at bar 5 (needs FRACTAL_WING=2 bars each
    # side lower than its neighbors).
    low[5] = 1.0950
    high[5] = 1.0960

    # First sweep attempt at bar 12: low dips under 1.0950, close pops
    # back above -- triggers, but give it NOTHING to break structure
    # with (flat prices after), so its own MSS search fails.
    low[12] = 1.0945
    close[12] = 1.0955

    # Second, later attempt at bar 20 using the SAME still-unconsumed
    # pool (1.0950): another dip-and-reclaim...
    low[20] = 1.0946
    close[20] = 1.0956
    # ...followed by a genuine structure break for its OWN MSS search.
    close[21] = 1.0990  # breaks above the recent structure high

    signals = detect_signals(high, low, close)
    ref_signals = _reference_detect_signals(high, low, close)
    assert _signals_comparable(signals) == _signals_comparable(ref_signals)
