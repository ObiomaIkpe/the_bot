"""
Unit tests for phase1/streaming/fvg_detector.py -- pure logic, no
database needed. Unlike every previous component, this one has no
confirmation delay to test -- the checks here are about the rolling
3-bar window being correct (which two bars it compares, that it slides
properly as new bars arrive) and that both directions work.

The exact-match validation against real historical data is documented
in PHASE1_VALIDATION.md: found 100% of the golden master's 2,138
fvg_found events with zero misses. All 520 extra events were traced
back to the two already-documented gaps from RaidDetector and
MSSWatch (extra raids, and MSS searches that continued past a fill) --
no new gap was introduced by this component.
"""
from phase1.streaming.fvg_detector import FVGDetector


def test_none_until_three_bars_fed():
    det = FVGDetector()
    det.on_new_bar(0, 1.10, 1.09)
    det.on_new_bar(1, 1.11, 1.10)
    assert det.check_fvg("t", "bull") is None


def test_bull_fvg_detected_with_correct_fields():
    det = FVGDetector()
    det.on_new_bar(0, 1.10, 1.05)  # high=1.10
    det.on_new_bar(1, 1.15, 1.08)  # middle candle -- values don't matter to the check
    det.on_new_bar(2, 1.20, 1.12)  # low=1.12 > bar 0's high 1.10 -> gap
    result = det.check_fvg("t", "bull")
    assert result is not None
    assert result["event_type"] == "fvg_found"
    assert result["direction"] == "bull"
    assert result["top"] == 1.12
    assert result["bottom"] == 1.10
    assert result["frame_idx"] == 0
    assert result["mss_bar_index"] == 2


def test_no_false_positive_when_candles_overlap():
    det = FVGDetector()
    det.on_new_bar(0, 1.10, 1.05)
    det.on_new_bar(1, 1.11, 1.09)
    det.on_new_bar(2, 1.12, 1.08)  # low=1.08 < bar 0's high 1.10 -- no gap
    assert det.check_fvg("t", "bull") is None


def test_bear_fvg_mirrors_bull():
    det = FVGDetector()
    det.on_new_bar(0, 1.20, 1.15)  # low=1.15
    det.on_new_bar(1, 1.13, 1.08)
    det.on_new_bar(2, 1.10, 1.05)  # high=1.10 < bar 0's low 1.15 -> gap
    result = det.check_fvg("t", "bear")
    assert result is not None
    assert result["direction"] == "bear"
    assert result["top"] == 1.15
    assert result["bottom"] == 1.10


def test_rolling_window_slides_correctly():
    """The 4th bar fed should push the 1st bar out of the window --
    the comparison must always be 'current vs. 2 bars back', not
    'current vs. the very first bar ever fed'."""
    det = FVGDetector()
    det.on_new_bar(0, 1.10, 1.05)
    det.on_new_bar(1, 1.11, 1.09)
    det.on_new_bar(2, 1.12, 1.08)  # no gap vs bar 0
    det.on_new_bar(3, 1.30, 1.25)  # now comparing bar 1 (high=1.11) vs bar 3 (low=1.25)
    result = det.check_fvg("t", "bull")
    assert result is not None
    assert result["frame_idx"] == 1  # NOT 0 -- the window has slid forward
    assert result["mss_bar_index"] == 3
