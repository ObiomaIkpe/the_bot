"""
Unit tests for phase1/streaming/raid_detector.py -- pure logic, no
database needed. These pin down the two subtlest things about this
class: the causality ordering (a swing confirmed at bar i can't be used
for bar i's own raid check) and the "both swing types must exist"
gating (an uptrend raid still requires a confirmed swing high, not just
the swing low it directly uses).

The exact-match check against real historical data is documented in
PHASE1_VALIDATION.md: RaidDetector found 100% of the golden master's
14,727 raid_detected events with zero misses. It also produced 2,312
additional raids beyond that -- every one of them verified to occur on
a day that already had a completed trade in the golden master, at a bar
index after golden master's last raid that day. This is expected: the
batch model stops scanning a day entirely once a trade completes
(`if trade_found: break`), which is a day-level behavior this class
doesn't implement (and shouldn't -- "has a trade already completed
today" isn't something raid detection can know on its own).
"""
from phase1.streaming.raid_detector import RaidDetector


def test_no_raid_without_any_confirmed_swings():
    det = RaidDetector()
    det.start_new_day()
    events = det.on_new_bar(
        timestamp="bar0", bar_index=0, high=1.20, low=1.00,
        direction="up", in_kill_zone=True, new_swing_events=[],
    )
    assert events == []


def test_same_bar_confirmation_not_usable_for_that_bars_own_check():
    """The core causality property: a swing confirmed exactly at this
    bar must not be usable for this same bar's raid check, even if the
    price condition would otherwise be met."""
    det = RaidDetector()
    det.start_new_day()
    events = det.on_new_bar(
        timestamp="bar0", bar_index=0, high=1.20, low=1.05,
        direction="up", in_kill_zone=True,
        new_swing_events=[
            {"event_type": "intraday_swing_low_confirmed", "bar_index": 0, "price": 1.10},
            {"event_type": "intraday_swing_high_confirmed", "bar_index": 0, "price": 1.20},
        ],
    )
    assert events == []


def test_confirmation_becomes_usable_on_the_next_bar():
    det = RaidDetector()
    det.start_new_day()
    det.on_new_bar(
        timestamp="bar0", bar_index=0, high=1.20, low=1.05,
        direction="up", in_kill_zone=True,
        new_swing_events=[
            {"event_type": "intraday_swing_low_confirmed", "bar_index": 0, "price": 1.10},
            {"event_type": "intraday_swing_high_confirmed", "bar_index": 0, "price": 1.20},
        ],
    )
    events = det.on_new_bar(
        timestamp="bar1", bar_index=1, high=1.15, low=1.05,  # breaks the 1.10 level
        direction="up", in_kill_zone=True, new_swing_events=[],
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "raid_detected"
    assert events[0]["direction"] == "bull"
    assert events[0]["raid_level"] == 1.10
    assert events[0]["raid_bar_low"] == 1.05


def test_uptrend_raid_requires_a_confirmed_swing_high_too():
    """Easy to miss: an uptrend raid only directly uses the swing LOW,
    but the batch model still requires a confirmed swing HIGH to exist
    before checking at all (it's needed for the MSS step that follows).
    Skipping this would let this class fire raids the batch model
    would have silently passed over."""
    det = RaidDetector()
    det.start_new_day()
    det.on_new_bar(
        timestamp="bar0", bar_index=0, high=1.20, low=1.05,
        direction="up", in_kill_zone=True,
        new_swing_events=[
            {"event_type": "intraday_swing_low_confirmed", "bar_index": 0, "price": 1.10},
            # deliberately no swing high confirmed
        ],
    )
    events = det.on_new_bar(
        timestamp="bar1", bar_index=1, high=1.15, low=1.05,
        direction="up", in_kill_zone=True, new_swing_events=[],
    )
    assert events == []


def test_no_raid_check_outside_kill_zone():
    det = RaidDetector()
    det.start_new_day()
    det.on_new_bar(
        timestamp="bar0", bar_index=0, high=1.20, low=1.05,
        direction="up", in_kill_zone=False,
        new_swing_events=[
            {"event_type": "intraday_swing_low_confirmed", "bar_index": 0, "price": 1.10},
            {"event_type": "intraday_swing_high_confirmed", "bar_index": 0, "price": 1.20},
        ],
    )
    events = det.on_new_bar(
        timestamp="bar1", bar_index=1, high=1.15, low=1.05,
        direction="up", in_kill_zone=False, new_swing_events=[],
    )
    assert events == []


def test_downtrend_raid_mirrors_uptrend():
    det = RaidDetector()
    det.start_new_day()
    det.on_new_bar(
        timestamp="bar0", bar_index=0, high=1.20, low=1.05,
        direction="down", in_kill_zone=True,
        new_swing_events=[
            {"event_type": "intraday_swing_low_confirmed", "bar_index": 0, "price": 1.05},
            {"event_type": "intraday_swing_high_confirmed", "bar_index": 0, "price": 1.20},
        ],
    )
    events = det.on_new_bar(
        timestamp="bar1", bar_index=1, high=1.25, low=1.10,  # breaks the 1.20 swing high
        direction="down", in_kill_zone=True, new_swing_events=[],
    )
    assert len(events) == 1
    assert events[0]["direction"] == "bear"
    assert events[0]["raid_level"] == 1.20
    assert events[0]["raid_bar_high"] == 1.25


def test_start_new_day_clears_state():
    det = RaidDetector()
    det.start_new_day()
    det.on_new_bar(
        timestamp="d1-bar0", bar_index=0, high=1.20, low=1.05,
        direction="up", in_kill_zone=True,
        new_swing_events=[
            {"event_type": "intraday_swing_low_confirmed", "bar_index": 0, "price": 1.10},
            {"event_type": "intraday_swing_high_confirmed", "bar_index": 0, "price": 1.20},
        ],
    )
    det.start_new_day()
    # same price condition that would have raided using yesterday's levels --
    # must NOT fire, since today has no confirmed swings yet
    events = det.on_new_bar(
        timestamp="d2-bar0", bar_index=0, high=1.15, low=1.05,
        direction="up", in_kill_zone=True, new_swing_events=[],
    )
    assert events == []
