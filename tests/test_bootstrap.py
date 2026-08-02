"""
Tests for Phase 3 step 7's cold-start trend bootstrap -- specifically
the three-way branch that's easy to get subtly wrong: fresh cold start
(inject data), already-bootstrapped (skip entirely), and pre-existing
real history from before this feature existed (mark done, but don't
inject anything on top of real data).
"""
import datetime

from app.models import Event
from shadow_runner.runner import ShadowRunner, NY_TZ
from tests.test_runner_orchestration import make_config
from tests.test_shadow_runner_recovery import RecoveryFakeDB, RecoveryFakeQuery


class TrackingFakeDB(RecoveryFakeDB):
    """Extends the recovery fake to also record what gets written via
    add(), so bootstrap's marker-event and swing-event writes are
    inspectable."""

    def __init__(self, event_rows=None):
        super().__init__(event_rows=event_rows)
        self.written_events = []

    def add(self, obj):
        self.written_events.append(obj)


def make_bar(date, hour, minute, high, low):
    return {
        "time_utc": datetime.datetime.combine(date, datetime.time(hour, minute)) - datetime.timedelta(hours=4),
        "time_ny": datetime.datetime.combine(date, datetime.time(hour, minute)),
        "open": (high + low) / 2, "high": high, "low": low, "close": (high + low) / 2,
        "tick_volume": 100, "spread": 8, "real_volume": 0,
    }


class FakeBridgeWithHistory:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, symbol, timeframe, count):
        return self._candles


def engineered_history_bars(start_date, num_days=9):
    """Same engineered peak/trough pattern as test_day_selection_gate's
    establish_up_trend, but expressed as raw M5 bars across num_days
    calendar days, so bootstrap's own aggregation logic gets exercised
    too (not just handed pre-aggregated highs/lows)."""
    highs = [1.10, 1.11, 1.15, 1.11, 1.10, 1.12, 1.20, 1.12, 1.10]
    lows = [1.05, 1.04, 1.00, 1.04, 1.09, 1.08, 1.03, 1.09, 1.10]
    bars = []
    d = start_date
    for i in range(num_days):
        # A handful of bars per day is enough -- aggregation just needs
        # the day's true high/low to appear somewhere in the set.
        bars.append(make_bar(d, 8, 0, highs[i], lows[i]))
        bars.append(make_bar(d, 8, 5, highs[i] - 0.001, lows[i] + 0.001))
        d += datetime.timedelta(days=1)
    return bars


def test_fresh_cold_start_injects_historical_data_and_writes_marker():
    config = make_config()
    db = TrackingFakeDB(event_rows=[])  # nothing exists at all yet
    start_date = datetime.datetime.now(NY_TZ).replace(tzinfo=None).date() - datetime.timedelta(days=20)
    bars = engineered_history_bars(start_date, num_days=9)
    bridge = FakeBridgeWithHistory(bars)
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)

    runner._bootstrap_trend_history_if_needed()

    marker_writes = [e for e in db.written_events if getattr(e, "event_type", None) == "trend_history_bootstrapped"]
    assert len(marker_writes) == 1
    assert marker_writes[0].details["days_seeded"] == 9

    # The gate itself should now have real trend data seeded via on_day_closed.
    assert len(runner.gate._confirmed_highs) >= 2
    assert len(runner.gate._confirmed_lows) >= 2


def test_already_bootstrapped_marker_prevents_any_reinjection():
    config = make_config()
    marker_event = Event(
        event_type="trend_history_bootstrapped", timestamp=datetime.datetime.now(),
        details={}, user_id="test-user-id", model="fvg",
    )
    db = TrackingFakeDB(event_rows=[marker_event])
    bridge = FakeBridgeWithHistory(candles=[])  # should never be called
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)

    runner._bootstrap_trend_history_if_needed()

    assert db.written_events == [], "should not write anything -- already bootstrapped"
    assert runner.gate._confirmed_highs == []  # gate untouched


def test_preexisting_real_swing_history_marks_done_without_injecting():
    config = make_config()
    real_swing_event = Event(
        event_type="daily_swing_high_confirmed",
        timestamp=datetime.datetime.now(),
        details={"price": 1.1234},
        user_id="test-user-id", model="fvg",
    )
    db = TrackingFakeDB(event_rows=[real_swing_event])  # no marker yet, but real data exists
    bridge = FakeBridgeWithHistory(candles=[])  # should never be called -- no injection should happen
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)

    runner._bootstrap_trend_history_if_needed()

    marker_writes = [e for e in db.written_events if getattr(e, "event_type", None) == "trend_history_bootstrapped"]
    assert len(marker_writes) == 1, "should still write the marker, just without injecting data"
    assert marker_writes[0].details["days_seeded"] == 0
    # No swing-confirmation events should have been written by bootstrap
    # itself (the pre-existing one in event_rows doesn't count -- that
    # was already there before this call).
    swing_writes = [e for e in db.written_events if getattr(e, "event_type", None) == "daily_swing_high_confirmed"]
    assert swing_writes == []