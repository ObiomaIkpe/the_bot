"""
Tests for cascade_raid/streaming/fractal_swings.py -- validates the
streaming FractalSwingDetector against a plain re-implementation of the
reference package's own vectorized find_fractal_swings() (reproduced
here inline, not imported -- the reference package lives outside this
repo and this project's own copy must stand alone per the "no shared
code between models" rule; reproducing the reference algorithm here
for test comparison is a deliberate, one-time exception scoped to
proving equivalence, not runtime sharing).
"""
import random

from cascade_raid.streaming.fractal_swings import (
    FractalSwingDetector,
    find_fractal_swings_streaming,
)


def _reference_find_fractal_swings(high, low, wing=2):
    """Direct reproduction of scalp_common.find_fractal_swings()'s exact
    algorithm, for test comparison only."""
    n = len(high)
    swing_high = [False] * n
    swing_low = [False] * n
    w = wing
    for i in range(w, n - w):
        window_h = high[i - w:i + w + 1]
        if high[i] == max(window_h) and window_h.count(high[i]) == 1:
            swing_high[i] = True
        window_l = low[i - w:i + w + 1]
        if low[i] == min(window_l) and window_l.count(low[i]) == 1:
            swing_low[i] = True
    return swing_high, swing_low


def test_confirms_a_simple_swing_high_exactly_wing_bars_later():
    # bars: 1,2,3,5,3,2,1 -- index 3 (value 5) is the unique max of any
    # 5-bar window centered on it.
    highs = [1, 2, 3, 5, 3, 2, 1]
    lows = [1, 2, 3, 5, 3, 2, 1]
    det = FractalSwingDetector(wing=2)
    results = [det.update(h, l) for h, l in zip(highs, lows)]

    # First 4 calls (idx 0-3) can't confirm anything yet (need 5 bars).
    assert results[:4] == [None, None, None, None]
    # 5th call (bar idx 4 arriving) confirms idx 2 (4 - wing=2).
    assert results[4]["idx"] == 2
    assert results[4]["swing_high"] is False  # bar 2 (value 3) is not the max
    # 6th call confirms idx 3 (value 5) -- IS the swing high.
    assert results[5]["idx"] == 3
    assert results[5]["swing_high"] is True
    assert results[5]["high"] == 5


def test_a_tied_maximum_confirms_neither_bar_as_a_swing_high():
    # Two bars sharing the window's max value -- reference explicitly
    # requires STRICT uniqueness (count == 1), matching MT5/backtest
    # tie-breaking: an ambiguous extreme is not a confirmed swing.
    highs = [1, 2, 5, 3, 5, 2, 1]
    lows = highs
    det = FractalSwingDetector(wing=2)
    results = [det.update(h, h) for h in highs]
    idx3_result = next(r for r in results if r is not None and r["idx"] == 3)
    assert idx3_result["swing_high"] is False


def test_streaming_output_matches_the_reference_batch_algorithm_on_random_data():
    random.seed(42)
    n = 500
    highs = [round(random.uniform(1.0, 1.2), 5) for _ in range(n)]
    lows = [h - round(random.uniform(0.0001, 0.001), 5) for h in highs]

    ref_high, ref_low = _reference_find_fractal_swings(highs, lows, wing=2)
    stream_high, stream_low = find_fractal_swings_streaming(highs, lows, wing=2)

    assert stream_high == ref_high
    assert stream_low == ref_low


def test_streaming_output_matches_reference_with_a_different_wing_size():
    random.seed(7)
    n = 300
    highs = [round(random.uniform(1.0, 1.2), 5) for _ in range(n)]
    lows = [h - round(random.uniform(0.0001, 0.001), 5) for h in highs]

    ref_high, ref_low = _reference_find_fractal_swings(highs, lows, wing=1)
    stream_high, stream_low = find_fractal_swings_streaming(highs, lows, wing=1)

    assert stream_high == ref_high
    assert stream_low == ref_low
