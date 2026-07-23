"""
Unit tests for phase1/streaming/mss_watch.py -- pure logic, no database
needed. Covers window boundaries (not before the raid, not after
expiry) and the fact that MSS can genuinely confirm more than once per
raid within its window.

The exact-match validation against real historical data is documented
in PHASE1_VALIDATION.md: MSSWatch found 100% of the golden master's
6,382 mss_confirmed events with zero misses. It also produced extra
events beyond that, ALL traced to one of two already-understood causes:
(1) raids RaidDetector over-produced (the day-level "stop after a
completed trade" gap already documented for that component), and (2)
for GENUINE golden-master raids, MSS confirmations at bars after the
point where that raid's own search already ended in a filled trade --
a new, narrower version of the same kind of gap, specific to one raid's
window rather than a whole day. Every single extra event in category
(2) -- 1,074 of them -- was individually verified to occur strictly
after golden master's own last logged MSS bar for that same raid.
"""
from phase1.streaming.mss_watch import MSSWatch


def test_no_confirmation_on_the_raids_own_bar():
    watch = MSSWatch(raid_bar_index=10, direction="bull", reference_level=1.10)
    events = watch.on_new_bar("t", 10, 1.20)  # same bar as the raid
    assert events == []


def test_confirms_when_close_crosses_reference_level():
    watch = MSSWatch(raid_bar_index=10, direction="bull", reference_level=1.10)
    events = watch.on_new_bar("t", 11, 1.11)
    assert len(events) == 1
    assert events[0]["event_type"] == "mss_confirmed"
    assert events[0]["mss_bar_index"] == 11


def test_can_confirm_more_than_once_in_the_same_window():
    """Not a first-time-only event -- the batch model keeps checking
    every bar in the window even after one confirms, if no valid trade
    resulted yet."""
    watch = MSSWatch(raid_bar_index=10, direction="bull", reference_level=1.10)
    events_a = watch.on_new_bar("t", 11, 1.11)
    events_b = watch.on_new_bar("t", 12, 1.12)
    assert len(events_a) == 1
    assert len(events_b) == 1


def test_expires_after_window_bars():
    watch = MSSWatch(raid_bar_index=10, direction="bull", reference_level=1.10)
    assert watch.is_expired(19) is False  # raid_bar_index + 9
    assert watch.is_expired(20) is True
    events = watch.on_new_bar("t", 20, 1.50)  # well past the level, but expired
    assert events == []


def test_bear_direction_mirrors_bull():
    watch = MSSWatch(raid_bar_index=5, direction="bear", reference_level=1.05)
    events = watch.on_new_bar("t", 6, 1.04)
    assert len(events) == 1
    assert events[0]["direction"] == "bear"


def test_no_confirmation_when_close_does_not_cross_level():
    watch = MSSWatch(raid_bar_index=10, direction="bull", reference_level=1.10)
    events = watch.on_new_bar("t", 11, 1.09)  # still below the level
    assert events == []
