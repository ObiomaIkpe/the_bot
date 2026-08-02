"""
Tests for Phase 3 step 6 (restart recovery): trend-history seeding from
already-journaled events, and the safe-to-replay-today vs.
already-partially-journaled-today decision.
"""
import datetime
from zoneinfo import ZoneInfo

import shadow_runner.persistence as persistence
from shadow_runner.runner import ShadowRunner, NY_TZ
from app.models import Event
from tests.test_runner_orchestration import make_config
from phase1.streaming.day_selection_gate import DaySelectionGate


class RecoveryFakeQuery:
    def __init__(self, all_result=None):
        self._all = all_result or []

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def all(self):
        return self._all


class RecoveryFakeDB:
    def __init__(self, event_rows=None):
        self.event_rows = event_rows or []
        self.added = []

    def query(self, model_cls):
        if model_cls is Event:
            return RecoveryFakeQuery(all_result=self.event_rows)
        raise AssertionError(f"unexpected query for {model_cls}")

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def close(self):
        pass


def make_swing_event(event_type, price, timestamp):
    return Event(event_type=event_type, timestamp=timestamp, details={"price": price})


def test_get_recent_swing_history_returns_last_two_oldest_first():
    highs = [
        make_swing_event("daily_swing_high_confirmed", 1.10, datetime.datetime(2026, 7, 1)),
        make_swing_event("daily_swing_high_confirmed", 1.15, datetime.datetime(2026, 7, 10)),
        make_swing_event("daily_swing_high_confirmed", 1.20, datetime.datetime(2026, 7, 20)),  # most recent
    ]
    # RecoveryFakeQuery ignores the actual filter, so just return
    # everything and let the persistence code's own ordering/limit
    # semantics do the real work -- but since our fake .all() returns
    # whatever we hand it regardless of order_by, hand it PRE-SORTED
    # newest-first, matching what a real DB would return for
    # .order_by(desc()).limit(2).
    newest_first_highs = sorted(highs, key=lambda e: e.timestamp, reverse=True)[:2]
    db = RecoveryFakeDB()
    db.query = lambda model_cls: RecoveryFakeQuery(all_result=newest_first_highs) if model_cls is Event else None

    confirmed_highs, confirmed_lows = persistence.get_recent_swing_history(db, "user1", "fvg")
    prices = [p for _, p in confirmed_highs]
    assert prices == [1.15, 1.20], "should be oldest-first, last 2 only"


def test_seed_trend_history_unblocks_gate_without_any_on_day_closed_calls():
    gate = DaySelectionGate()
    # Higher-high, higher-low -> "up", seeded directly, no on_day_closed() ever called.
    gate.seed_trend_history(
        confirmed_highs=[(0, 1.15), (1, 1.20)],
        confirmed_lows=[(0, 1.00), (1, 1.03)],
    )
    trend = gate._trend_for_today()
    assert trend == "up"


def _today_ny():
    return datetime.datetime.now(NY_TZ).replace(tzinfo=None).date()


def make_bar(date, hour, minute, close=1.1000):
    dt = datetime.datetime.combine(date, datetime.time(hour, minute))
    return {
        "time_utc": dt - datetime.timedelta(hours=4),
        "time_ny": dt,
        "open": close, "high": close + 0.0005, "low": close - 0.0005, "close": close,
        "tick_volume": 100, "spread": 8, "real_volume": 0,
    }


class FakeBridgeForReplay:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, symbol, timeframe, count):
        return self._candles


def test_recovery_replays_when_nothing_journaled_yet_today():
    today = _today_ny()
    now = datetime.datetime.now(NY_TZ).replace(tzinfo=None)
    # Bars from 5am today up to (but comfortably before) right now,
    # every one of them already closed.
    safe_cutoff = now - datetime.timedelta(minutes=10)
    bars = []
    t = datetime.datetime.combine(today, datetime.time(5, 0))
    while t < safe_cutoff and t.time() < datetime.time(17, 0):
        bars.append(
            {
                "time_utc": t - datetime.timedelta(hours=4),
                "time_ny": t,
                "open": 1.10, "high": 1.1005, "low": 1.0995, "close": 1.10,
                "tick_volume": 100, "spread": 8, "real_volume": 0,
            }
        )
        t += datetime.timedelta(minutes=5)

    config = make_config()
    db = RecoveryFakeDB(event_rows=[])  # nothing journaled at all yet
    bridge = FakeBridgeForReplay(bars)
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)

    runner.recover_on_startup()

    assert runner.current_day is not None, "replay should have created today's CurrentDay"
    assert runner.current_day.date == today
    assert len(runner.current_day.bars) > 0, "replay should have fed at least some bars"


def test_recovery_skips_replay_when_events_already_exist_today():
    today = _today_ny()
    existing_event = Event(
        event_type="raid_detected",
        timestamp=datetime.datetime.combine(today, datetime.time(8, 0)),
        details={"price": 1.10},  # RecoveryFakeDB doesn't filter by event_type
                                    # like a real DB would, so get_recent_swing_history's
                                    # query sees this too -- give it a harmless "price"
                                    # key so that doesn't crash; the actual thing this
                                    # test verifies (replay gets skipped) is unaffected.
    )
    config = make_config()
    db = RecoveryFakeDB(event_rows=[existing_event])
    bridge = FakeBridgeForReplay(candles=[])  # should never even be called
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)

    runner.recover_on_startup()

    assert runner.current_day is None, "should NOT replay when today already has journaled events"