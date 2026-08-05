"""
Regression tests for two real bugs found running the shadow runner live
on Hetzner for the first time (cold start on a weekend):

1. The bootstrap marker event was polluting the "has today already
   started" check -- every cold start looked like it had already
   partially processed today, even though nothing real had happened.
2. Cold-starting when the bridge's most recent bars belong to an
   already-finished day (e.g. Friday's tail end, seen on a Sunday)
   caused the runner to try to construct and judge a day from a tiny
   fragment, producing a misleading "insufficient_bars" verdict.
"""
import datetime

from app.models import Event
from shadow_runner.runner import ShadowRunner, NY_TZ
from shadow_runner.persistence import get_last_event_timestamp_for_date
from tests.shadow_runner.test_runner_orchestration import make_config
from tests.shadow_runner.test_shadow_runner_recovery import RecoveryFakeDB


def test_bootstrap_marker_excluded_from_todays_activity_check():
    """Bug 1. A bootstrap marker timestamped today must NOT make
    get_last_event_timestamp_for_date() think today already has real
    activity."""
    today = datetime.datetime.now(NY_TZ).replace(tzinfo=None).date()
    marker_only = Event(
        event_type="trend_history_bootstrapped",
        timestamp=datetime.datetime.combine(today, datetime.time(8, 0)),
        details={"days_seeded": 21},
        user_id="test-user-id", model="fvg",
    )
    db = RecoveryFakeDB(event_rows=[marker_only])

    result = get_last_event_timestamp_for_date(db, "test-user-id", "fvg", today)
    assert result is None, "bootstrap marker alone should NOT count as today's activity"


def test_bootstrap_marker_ignored_but_real_event_same_day_still_detected():
    """The exclusion should be specific to the marker type -- a REAL
    event on the same day must still be detected normally."""
    today = datetime.datetime.now(NY_TZ).replace(tzinfo=None).date()
    marker = Event(
        event_type="trend_history_bootstrapped",
        timestamp=datetime.datetime.combine(today, datetime.time(8, 0)),
        details={}, user_id="test-user-id", model="fvg",
    )
    real_event = Event(
        event_type="raid_detected",
        timestamp=datetime.datetime.combine(today, datetime.time(9, 0)),
        details={}, user_id="test-user-id", model="fvg",
    )
    db = RecoveryFakeDB(event_rows=[real_event, marker])

    result = get_last_event_timestamp_for_date(db, "test-user-id", "fvg", today)
    assert result == real_event.timestamp


def make_bar(date, hour, minute):
    dt = datetime.datetime.combine(date, datetime.time(hour, minute))
    return {
        "time_utc": dt - datetime.timedelta(hours=4),
        "time_ny": dt,
        "open": 1.10, "high": 1.1005, "low": 1.0995, "close": 1.10,
        "tick_volume": 100, "spread": 8, "real_volume": 0,
    }


def test_stale_bar_from_finished_day_is_ignored_on_cold_start():
    """Bug 2. A bar dated before today (e.g. Friday's tail end, first
    bar seen on a Sunday cold start) must not create a CurrentDay at
    all -- not even one that gets immediately skipped for
    insufficient_bars."""
    config = make_config()
    db = RecoveryFakeDB(event_rows=[])
    runner = ShadowRunner(config, bridge=None, session_factory=lambda: db)

    yesterday = datetime.datetime.now(NY_TZ).replace(tzinfo=None).date() - datetime.timedelta(days=2)
    stale_bar = make_bar(yesterday, 16, 55)  # Friday's last bar, say

    runner._process_bar(stale_bar)

    assert runner.current_day is None, "a stale bar must not create a CurrentDay at all"


def test_bar_dated_today_still_starts_a_day_normally():
    """Make sure the fix doesn't accidentally block genuinely current
    bars too -- only strictly-past dates should be ignored."""
    config = make_config()
    db = RecoveryFakeDB(event_rows=[])
    runner = ShadowRunner(config, bridge=None, session_factory=lambda: db)

    today = datetime.datetime.now(NY_TZ).replace(tzinfo=None).date()
    fresh_bar = make_bar(today, 5, 0)

    runner._process_bar(fresh_bar)

    assert runner.current_day is not None
    assert runner.current_day.date == today