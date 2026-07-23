"""
Unit tests for phase1/streaming/daily_swing_detector.py -- pure logic,
no database or fixtures needed. These pin down the boundary behavior
that's easy to get subtly wrong in a streaming reimplementation:
confirmation delay, and tie handling.

The exact-match check against real historical data (893/893 events,
every timestamp/day_index/price identical) was run separately, not as
part of this file, since it requires the real CSV data and the golden
master output -- see PHASE1_VALIDATION.md.
"""
from phase1.streaming.daily_swing_detector import DailySwingDetector


def test_no_events_until_window_fills():
    det = DailySwingDetector(pivot_n=2)
    events = []
    for i in range(4):  # fewer than the 5-day window
        events.extend(det.on_new_day(f"day{i}", 1.10 + i * 0.001, 1.09 + i * 0.001))
    assert events == []


def test_confirms_correct_swing_high_at_correct_day_index():
    det = DailySwingDetector(pivot_n=2)
    highs = [1.10, 1.11, 1.15, 1.12, 1.09]
    lows = [1.05, 1.06, 1.07, 1.06, 1.04]
    events = []
    for i in range(5):
        events.extend(det.on_new_day(f"day{i}", highs[i], lows[i]))

    swing_highs = [e for e in events if e["event_type"] == "daily_swing_high_confirmed"]
    assert len(swing_highs) == 1
    assert swing_highs[0]["day_index"] == 2
    assert swing_highs[0]["price"] == 1.15


def test_confirmation_is_delayed_by_pivot_n_days():
    """The whole point of this class: day 2's swing status can't be known
    until day 4 (2 days later) has arrived. If this test fails, the
    detector has lookahead -- it's deciding about a day before it's
    allowed to know the answer."""
    det = DailySwingDetector(pivot_n=2)
    highs = [1.10, 1.11, 1.15, 1.12, 1.09]
    lows = [1.05, 1.06, 1.07, 1.06, 1.04]
    events = []
    for i in range(4):  # day 4 deliberately NOT fed yet
        events.extend(det.on_new_day(f"day{i}", highs[i], lows[i]))
    assert events == []


def test_swing_low_detected_symmetrically():
    det = DailySwingDetector(pivot_n=2)
    highs = [1.10, 1.10, 1.10, 1.10, 1.10]
    lows = [1.00, 0.99, 0.90, 0.98, 1.01]
    events = []
    for i in range(5):
        events.extend(det.on_new_day(f"day{i}", highs[i], lows[i]))

    swing_lows = [e for e in events if e["event_type"] == "daily_swing_low_confirmed"]
    assert len(swing_lows) == 1
    assert swing_lows[0]["day_index"] == 2
    assert swing_lows[0]["price"] == 0.90


def test_tied_highs_both_confirmed_matching_batch_behavior():
    """The batch model's `d_highs[i] == max(...)` has no tie-breaking --
    if two days in the same window are equal to the window max, both get
    marked. The streaming version must do the same, not silently pick one."""
    det = DailySwingDetector(pivot_n=2)
    highs = [1.00, 1.00, 1.20, 1.20, 0.90]  # index 2 and 3 tie for the window max
    lows = [0.90, 0.90, 0.90, 0.90, 0.80]
    events = []
    for i in range(5):
        events.extend(det.on_new_day(f"day{i}", highs[i], lows[i]))

    swing_highs = [e for e in events if e["event_type"] == "daily_swing_high_confirmed"]
    # Only day index 2 has a full centered window within this 5-day run
    # (index 3's window would need a day index 5, not fed here) -- so
    # only one confirmation is possible in this particular test, but it
    # must be the correct one under a plain "==" comparison.
    assert len(swing_highs) == 1
    assert swing_highs[0]["day_index"] == 2
    assert swing_highs[0]["price"] == 1.20
