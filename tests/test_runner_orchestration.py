"""
Integration-style tests for shadow_runner.runner's per-bar orchestration
logic -- the part most likely to have a subtle bug (day rollover timing,
the 10am decision gate, backfill happening exactly once). Uses a fake DB
session (no real Postgres) and drives _process_bar directly rather than
through poll_once/the real bridge, so these run without any network
access.
"""
import datetime

from shadow_runner.config import ShadowRunnerConfig
from shadow_runner.runner import ShadowRunner, NY_TZ
import shadow_runner.persistence as persistence
from app.models import UserSettings


class FakeQuery:
    def __init__(self, one_result=None, first_result=None):
        self._one = one_result
        self._first = first_result

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def one(self):
        return self._one

    def first(self):
        return self._first


class FakeDB:
    """One instance per session_factory() call, matching real
    SessionLocal() behavior -- writes accumulate into the SHARED list
    passed in, so a test can inspect everything written across many
    open/commit/close cycles."""

    def __init__(self, shared_writes, risk_pct=0.01):
        self.shared_writes = shared_writes
        self.risk_pct = risk_pct

    def query(self, model_cls):
        if model_cls is UserSettings:
            return FakeQuery(one_result=UserSettings(risk_pct=self.risk_pct))
        # Trade query (get_current_equity) -- no prior trades in these tests
        return FakeQuery(first_result=None)

    def add(self, obj):
        self.shared_writes.append(obj)

    def commit(self):
        pass

    def close(self):
        pass


def make_config():
    import os
    os.environ["BRIDGE_URL"] = "http://fake-bridge:8001"
    os.environ["SHADOW_RUNNER_USER_ID"] = "test-user-id"
    return ShadowRunnerConfig()


def make_bar(date, hour, minute, o, h, l, c):
    return {
        "time_utc": datetime.datetime.combine(date, datetime.time(hour, minute)),
        "time_ny": datetime.datetime.combine(date, datetime.time(hour, minute)),
        "open": o, "high": h, "low": l, "close": c,
        "tick_volume": 100, "spread": 8, "real_volume": 0,
    }


def full_day_bars(date, base=1.1000):
    """5am-5pm, 5-minute bars, flat price (no trade signals expected --
    this test is about TIMING, not trade detection)."""
    bars = []
    t = datetime.datetime.combine(date, datetime.time(5, 0))
    end = datetime.datetime.combine(date, datetime.time(17, 0))
    while t <= end:
        bars.append(make_bar(date, t.hour, t.minute, base, base + 0.0005, base - 0.0005, base))
        t += datetime.timedelta(minutes=5)
    return bars


def establish_trend(gate):
    """Same engineered peak/trough data as test_day_selection_gate.py's
    establish_up_trend, reused here so gate_for_day() actually returns
    tradeable=True partway through the test.

    Anchored off the REAL current date (not a hardcoded past date) so
    the returned next_date lands on today -- otherwise ShadowRunner's
    cold-start guard (added after a real bug: don't try to construct a
    day from a stale bar belonging to an already-finished day) would
    correctly reject these bars as "from the past," which would be
    right in general but wrong for what THIS test is actually checking
    (day-rollover/decision-timing logic, not cold-start behavior)."""
    highs = [1.10, 1.11, 1.15, 1.11, 1.10, 1.12, 1.20, 1.12, 1.10]
    lows = [1.05, 1.04, 1.00, 1.04, 1.09, 1.08, 1.03, 1.09, 1.10]
    import shadow_runner.runner as runner_module
    d = datetime.datetime.now(runner_module.NY_TZ).replace(tzinfo=None).date() - datetime.timedelta(days=9)
    for i in range(9):
        gate.on_day_closed(d, highs[i], lows[i])
        d += datetime.timedelta(days=1)
    return d


def test_decision_deferred_until_10am_then_backfills_everything_at_once():
    shared_writes = []
    config = make_config()
    runner = ShadowRunner(config, bridge=None, session_factory=lambda: FakeDB(shared_writes))
    next_date = establish_trend(runner.gate)

    bars = full_day_bars(next_date)
    ten_am = datetime.time(10, 0)

    bars_before_10am = [b for b in bars if b["time_ny"].time() < ten_am]
    bars_from_10am_on = [b for b in bars if b["time_ny"].time() >= ten_am]

    for b in bars_before_10am:
        runner._process_bar(b)
        assert runner.current_day.decided is False, (
            f"decided too early, at {b['time_ny']} -- should wait for a bar >= 10:00"
        )
        assert runner.current_day.orchestrator is None

    # The very next bar (first one >= 10am) should trigger the decision
    # AND backfill every bar seen so far, all in one call.
    first_10am_bar = bars_from_10am_on[0]
    runner._process_bar(first_10am_bar)

    assert runner.current_day.decided is True
    assert runner.current_day.tradeable is True  # engineered uptrend
    assert runner.current_day.orchestrator is not None
    # Backfill should have covered every bar up to and including this one.
    assert len(runner.current_day.bars) == len(bars_before_10am) + 1

    day_trend_events = [
        e for e in runner.current_day.todays_events if e.get("event_type") == "day_trend_determined"
    ]
    assert len(day_trend_events) == 1
    assert day_trend_events[0]["trend"] == "up"

    # Feed the rest of the day one bar at a time -- should NOT re-decide
    # or re-backfill (that would corrupt DayOrchestrator's internal state
    # by feeding bars twice).
    for b in bars_from_10am_on[1:]:
        runner._process_bar(b)
    # is_session_bar() uses a half-open [5:00, 17:00) window, so the one
    # bar timestamped exactly 17:00 in full_day_bars() is correctly
    # excluded -- matching the batch script's day_end slicing convention.
    assert len(runner.current_day.bars) == len(bars) - 1


def test_skipped_day_journals_skip_reason_and_never_constructs_orchestrator():
    shared_writes = []
    config = make_config()
    runner = ShadowRunner(config, bridge=None, session_factory=lambda: FakeDB(shared_writes))
    # No trend established -- gate_for_day() must return "no_trend".
    # Anchored off the REAL current date (not a hardcoded one) -- a fixed
    # date eventually becomes "the past" as real time moves on, which the
    # cold-start stale-bar guard (added after a real bug -- see
    # PHASE3_RESTART_RECOVERY.md addendum 2) correctly rejects. This
    # test is about the no_trend skip path, not cold-start behavior, so
    # it needs a date that's never in the past relative to whenever the
    # test actually runs.
    d = datetime.datetime.now(NY_TZ).replace(tzinfo=None).date()
    bars = full_day_bars(d)
    for b in bars:
        runner._process_bar(b)

    assert runner.current_day.decided is True
    assert runner.current_day.tradeable is False
    assert runner.current_day.skip_reason == "no_trend"
    assert runner.current_day.orchestrator is None

    skip_writes = [w for w in shared_writes if getattr(w, "event_type", None) == "day_skipped_no_trend"]
    assert len(skip_writes) == 1


def test_day_rollover_finalizes_previous_day_and_feeds_daily_swing():
    shared_writes = []
    config = make_config()
    runner = ShadowRunner(config, bridge=None, session_factory=lambda: FakeDB(shared_writes))
    next_date = establish_trend(runner.gate)

    day1_bars = full_day_bars(next_date)
    for b in day1_bars:
        runner._process_bar(b)
    assert runner.current_day.date == next_date
    assert runner.current_day.tradeable is True

    # First bar of the NEXT day should trigger finalize() on day 1 before
    # starting day 2's CurrentDay.
    day2_date = next_date + datetime.timedelta(days=1)
    first_day2_bar = make_bar(day2_date, 5, 0, 1.10, 1.1005, 1.0995, 1.10)
    runner._process_bar(first_day2_bar)

    assert runner.current_day.date == day2_date
    assert runner.current_day.decided is False  # fresh day, not yet decided

    # Day 1's daily high/low should have been fed into the gate via
    # on_day_closed -- confirmed indirectly: the gate's internal closed-day
    # counter advanced.
    assert runner.gate._closed_day_count == 10  # 9 from establish_trend + day 1 itself