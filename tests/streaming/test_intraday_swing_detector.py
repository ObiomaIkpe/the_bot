"""
Unit tests for phase1/streaming/intraday_swing_detector.py -- pure
logic, no database needed. Covers the same boundary behavior as
test_daily_swing_detector.py (window-fill delay, tie handling) plus the
one thing that's new and specific to this class: start_new_day()
actually clearing memory between days.

The exact-match check against real historical data (63,664/63,664
events matched the golden master exactly) was run separately, since it
requires replicating the batch model's full day-selection logic
(FOMC/trend/session-start gates) -- see PHASE1_VALIDATION.md.
"""
from phase1.streaming.intraday_swing_detector import IntradaySwingDetector


def test_no_events_until_window_fills():
    det = IntradaySwingDetector(swing_n=2)
    det.start_new_day()
    events = []
    for i in range(4):
        events.extend(det.on_new_bar(f"bar{i}", 1.10 + i * 0.001, 1.09 + i * 0.001))
    assert events == []


def test_confirms_correct_swing_high_at_correct_bar_index():
    det = IntradaySwingDetector(swing_n=2)
    det.start_new_day()
    highs = [1.10, 1.11, 1.15, 1.12, 1.09]
    lows = [1.05, 1.06, 1.07, 1.06, 1.04]
    events = []
    for i in range(5):
        events.extend(det.on_new_bar(f"bar{i}", highs[i], lows[i]))

    swing_highs = [e for e in events if e["event_type"] == "intraday_swing_high_confirmed"]
    assert len(swing_highs) == 1
    assert swing_highs[0]["bar_index"] == 2
    assert swing_highs[0]["price"] == 1.15


def test_start_new_day_clears_previous_days_window():
    """The behavior that's genuinely new versus the daily detector: this
    must not remember anything about yesterday. If this test fails, a
    swing from the end of one day could get confirmed using bars from
    the start of the next -- exactly the kind of cross-day leakage the
    batch model's per-day recomputation never allows."""
    det = IntradaySwingDetector(swing_n=2)

    det.start_new_day()
    for i in range(5):
        det.on_new_bar(f"day1-bar{i}", 1.50, 1.40)  # deliberately extreme values

    det.start_new_day()
    events_day2 = []
    for i in range(4):  # fewer than 5 bars -- should be impossible to confirm anything
        events_day2.extend(det.on_new_bar(f"day2-bar{i}", 1.10, 1.09))
    assert events_day2 == [], (
        "day 2 produced an event with fewer than 5 bars fed -- "
        "day 1's window was not cleared by start_new_day()"
    )


def test_bar_index_resets_to_zero_each_day():
    det = IntradaySwingDetector(swing_n=2)

    det.start_new_day()
    for i in range(6):  # push bar_index up into day 1
        det.on_new_bar(f"day1-bar{i}", 1.10, 1.09)

    det.start_new_day()
    highs = [1.10, 1.11, 1.15, 1.12, 1.09]
    lows = [1.05, 1.06, 1.07, 1.06, 1.04]
    events = []
    for i in range(5):
        events.extend(det.on_new_bar(f"day2-bar{i}", highs[i], lows[i]))

    swing_highs = [e for e in events if e["event_type"] == "intraday_swing_high_confirmed"]
    assert len(swing_highs) == 1
    assert swing_highs[0]["bar_index"] == 2  # not 8 -- counts from 0 within day 2


def test_tied_highs_both_confirmed_matching_batch_behavior():
    det = IntradaySwingDetector(swing_n=2)
    det.start_new_day()
    highs = [1.00, 1.00, 1.20, 1.20, 0.90]
    lows = [0.90, 0.90, 0.90, 0.90, 0.80]
    events = []
    for i in range(5):
        events.extend(det.on_new_bar(f"bar{i}", highs[i], lows[i]))

    swing_highs = [e for e in events if e["event_type"] == "intraday_swing_high_confirmed"]
    assert len(swing_highs) == 1
    assert swing_highs[0]["bar_index"] == 2
    assert swing_highs[0]["price"] == 1.20
