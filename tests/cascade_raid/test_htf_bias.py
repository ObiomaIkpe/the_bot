"""
Tests for cascade_raid/streaming/htf_bias.py -- validates against a
direct reproduction of the reference package's htf_bias_cascade.py
functions (reproduced inline for comparison only -- see
test_fractal_swings.py's own docstring for why).
"""
import random

from cascade_raid.streaming.htf_bias import (
    FractalBiasTracker,
    classify_case,
    fractal_bias_series_streaming,
    full_agreement,
)


def _ref_fractal_bias_series(closes, wing=1):
    n = len(closes)
    bias = [None] * n
    current = None
    span = 2 * wing
    for i in range(span, n):
        window = closes[i - span:i + 1]
        mid = closes[i - wing]
        if mid == min(window) and window.count(mid) == 1:
            current = "bullish"
        elif mid == max(window) and window.count(mid) == 1:
            current = "bearish"
        bias[i] = current
    return bias


def test_matches_reference_bias_series_wing1_random_data():
    random.seed(3)
    closes = [round(random.uniform(1.0, 1.2), 5) for _ in range(400)]
    ref = _ref_fractal_bias_series(closes, wing=1)
    stream = fractal_bias_series_streaming(closes, wing=1)
    assert stream == ref


def test_matches_reference_bias_series_wing2():
    random.seed(11)
    closes = [round(random.uniform(1.0, 1.2), 5) for _ in range(400)]
    ref = _ref_fractal_bias_series(closes, wing=2)
    stream = fractal_bias_series_streaming(closes, wing=2)
    assert stream == ref


def test_none_until_first_confirmation():
    tracker = FractalBiasTracker(wing=1)
    assert tracker.update(1.10) is None  # span+1=3 closes needed, only 1 so far
    assert tracker.update(1.11) is None  # 2 so far


def test_simple_bullish_swing_low_confirms_bullish():
    tracker = FractalBiasTracker(wing=1)
    tracker.update(1.10)
    tracker.update(1.05)  # local low
    result = tracker.update(1.12)  # closes[i-1]=1.05 is < both neighbors -> bullish
    assert result == "bullish"


def test_simple_bearish_swing_high_confirms_bearish():
    tracker = FractalBiasTracker(wing=1)
    tracker.update(1.10)
    tracker.update(1.20)  # local high
    result = tracker.update(1.08)
    assert result == "bearish"


def test_bias_is_sticky_until_a_new_confirmation_flips_it():
    tracker = FractalBiasTracker(wing=1)
    tracker.update(1.10)
    tracker.update(1.05)
    assert tracker.update(1.12) == "bullish"
    # Window is now [1.05, 1.12, new] -- for 1.12 (the fixed middle
    # position) to be neither a new min nor max, `new` must land above
    # it, making 1.12 the window's median (a non-confirming bar).
    assert tracker.update(1.14) == "bullish"


# ---------- classify_case / full_agreement ----------

def test_full_agreement_requires_all_three_and_a_non_none_daily():
    assert full_agreement("bullish", "bullish", "bullish") == "bullish"
    assert full_agreement("bullish", "bullish", "bearish") is None
    assert full_agreement(None, "bullish", "bullish") is None
    assert full_agreement("bullish", None, "bullish") is None


def test_classify_case1_full_agreement_matches_signal_direction():
    assert classify_case("bullish", "bullish", "bullish", "long") == "case1"
    assert classify_case("bearish", "bearish", "bearish", "short") == "case1"
    assert classify_case("bullish", "bullish", "bullish", "short") != "case1"


def test_classify_case2_monthly_weekly_agree_daily_diverges():
    assert classify_case("bullish", "bullish", "bearish", "long") == "case2"


def test_classify_case3_weekly_daily_agree_against_monthly():
    assert classify_case("bullish", "bearish", "bearish", "long") == "case3"


def test_classify_returns_none_when_nothing_matches():
    assert classify_case("bearish", "bullish", None, "long") is None
