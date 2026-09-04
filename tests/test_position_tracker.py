"""
Tests for shadow_runner.position_tracker.PositionTracker -- the
overnight-position handling built to satisfy the confirmed rule: real
trades run to natural resolution instead of force-closing at day_end,
but half the volume gets closed the first time a position is still open
past 5pm NY.

Three things get direct coverage, since this is real-money-adjacent
logic:
  1. The partial close actually fires at the right threshold, and only
     once.
  2. A fully-closed (vanished) position gets correctly finalized,
     including the history-cache-lag race (same pattern as
     OrderManager.check_for_close()).
  3. load_from_db() correctly reconstructs tracking state -- this IS
     the cross-restart resilience the whole file exists for; if this is
     wrong, a restart mid-multi-day-trade silently orphans real money.
"""
import datetime

from shadow_runner.position_tracker import PositionTracker


class FakePositionBridge:
    """Minimal fake bridge -- only what PositionTracker actually calls."""

    def __init__(self):
        self.partial_closes = []  # list of (ticket, volume)
        self._positions = {}      # ticket -> dict
        self._closed_history = {}  # ticket -> dict

    def get_positions(self, magic):
        return [p for p in self._positions.values() if p["magic"] == magic]

    def close_position_partial(self, ticket, volume):
        self.partial_closes.append((ticket, volume))
        pos = self._positions[ticket]
        remaining = round(pos["volume"] - volume, 8)
        pos["volume"] = remaining
        return {
            "ticket": ticket, "closed_volume": volume, "close_price": pos["current_price"],
            "remaining_volume": remaining,
            "time_utc": "2026-08-04T21:00:00+00:00", "time_ny": "2026-08-04T17:00:00-04:00",
            "retcode": 10009, "broker_comment": "ok",
        }

    def get_position_history(self, ticket):
        return self._closed_history.get(ticket, {"ticket": ticket, "is_closed": False})

    # ---- test helpers ----

    def add_open_position(self, ticket, magic, volume=0.02, current_price=1.1080):
        self._positions[ticket] = {
            "ticket": ticket, "magic": magic, "volume": volume, "current_price": current_price,
        }

    def simulate_vanish(self, ticket, close_price=None, profit=None, close_reason=None, history_ready=True):
        self._positions.pop(ticket, None)
        if history_ready:
            self._closed_history[ticket] = {
                "ticket": ticket, "is_closed": True, "close_price": close_price,
                "close_time_utc": "x", "close_time_ny": "x", "profit": profit, "close_reason": close_reason,
            }


class FakeTradeRow:
    def __init__(self, trade_id, real_position_ticket, real_status, entry_time_ny, direction="long"):
        self.trade_id = trade_id
        self.real_position_ticket = real_position_ticket
        self.real_status = real_status
        self.entry_time_ny = entry_time_ny
        self.direction = direction
        # Fields update_trade_partial_close/update_trade_final_close mutate:
        self.partial_close_price = None
        self.partial_close_time_utc = None
        self.partial_close_time_ny = None
        self.partial_close_volume = None
        self.partial_close_profit = None
        self.real_close_price = None
        self.real_close_time_utc = None
        self.real_close_time_ny = None
        self.real_profit = None
        self.real_close_reason = None


class FakeQuery:
    """Real filtering by trade_id (via _FakeCondition, same pattern as
    tests/test_shadow_runner_recovery.py's RecoveryFakeQuery) -- needed
    because update_trade_partial_close/update_trade_final_close must
    find and MUTATE one specific row, not just return whatever's first."""

    def __init__(self, rows_by_trade_id: dict):
        self._rows_by_trade_id = rows_by_trade_id
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def _matching_rows(self):
        rows = list(self._rows_by_trade_id.values())
        for cond in self._conditions:
            # Only trade_id filtering is exercised by this module's
            # functions -- other conditions (if any were ever added)
            # would need real handling too, not attempted here.
            if getattr(cond, "name", None) == "trade_id":
                rows = [r for r in rows if r.trade_id == cond.value]
        return rows

    def all(self):
        return self._matching_rows()

    def one(self):
        matches = self._matching_rows()
        return matches[0]


class FakeDB:
    """Just enough to satisfy persistence.get_open_real_trades /
    update_trade_partial_close / update_trade_final_close / write_event,
    tracking what got written so tests can assert on it. Rows are keyed
    by trade_id and genuinely mutated by update_* calls, matching how a
    real SQLAlchemy session would behave for these functions."""

    def __init__(self, rows=None):
        self._rows_by_trade_id = {r.trade_id: r for r in (rows or [])}
        self.added_events = []
        self.committed = False

    def query(self, model_cls):
        return FakeQuery(self._rows_by_trade_id)

    def add(self, obj):
        self.added_events.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def make_model_config(magic=900001):
    return {"model_name": "fvg", "status": "active", "magic_number": magic}


def test_register_new_position_tracks_it():
    bridge = FakePositionBridge()
    tracker = PositionTracker(bridge, lambda: FakeDB(), "user1", make_model_config())
    entry_time = datetime.datetime(2026, 8, 4, 9, 30)
    tracker.register_new_position(2001, "trade-abc", entry_time)
    assert 2001 in tracker._tracked
    assert tracker._tracked[2001]["partial_closed"] is False


def test_check_positions_does_nothing_before_5pm_threshold():
    bridge = FakePositionBridge()
    bridge.add_open_position(2001, magic=900001)
    tracker = PositionTracker(bridge, lambda: FakeDB(), "user1", make_model_config())

    # Entry time is "today" -- but real wall-clock "now" inside
    # check_positions() is whatever it actually is when the test runs,
    # which could be before OR after 5pm. To make this deterministic,
    # register a position with an entry time far in the FUTURE relative
    # to real now, guaranteeing "now" is still before its 5pm threshold.
    far_future_entry = datetime.datetime.now() + datetime.timedelta(days=365)
    tracker.register_new_position(2001, "trade-abc", far_future_entry)

    tracker.check_positions()
    assert bridge.partial_closes == [], "must not partial-close before the 5pm threshold"


def test_check_positions_partial_closes_once_past_threshold():
    bridge = FakePositionBridge()
    bridge.add_open_position(2001, magic=900001, volume=0.02)
    # session_factory must return the SAME db instance every call --
    # PositionTracker opens a fresh "session" per operation (matching
    # real SQLAlchemy usage), so a lambda that builds a NEW empty FakeDB
    # each time would lose the row between calls, same as a real DB
    # wouldn't.
    far_past_entry = datetime.datetime.now() - datetime.timedelta(days=5)
    db = FakeDB(rows=[FakeTradeRow("trade-abc", 2001, "open", far_past_entry)])
    tracker = PositionTracker(bridge, lambda: db, "user1", make_model_config())
    tracker.register_new_position(2001, "trade-abc", far_past_entry)

    tracker.check_positions()
    assert len(bridge.partial_closes) == 1
    ticket, volume = bridge.partial_closes[0]
    assert ticket == 2001
    assert volume == 0.01  # half of 0.02
    assert tracker._tracked[2001]["partial_closed"] is True
    assert db._rows_by_trade_id["trade-abc"].real_status == "partial_closed"

    # A second poll cycle must NOT partial-close again.
    tracker.check_positions()
    assert len(bridge.partial_closes) == 1, "must only partial-close once per trade"


def test_check_positions_detects_full_close_and_stops_tracking():
    bridge = FakePositionBridge()
    bridge.add_open_position(2001, magic=900001)
    entry_time = datetime.datetime.now()
    db = FakeDB(rows=[FakeTradeRow("trade-abc", 2001, "open", entry_time)])
    tracker = PositionTracker(bridge, lambda: db, "user1", make_model_config())
    tracker.register_new_position(2001, "trade-abc", entry_time)

    bridge.simulate_vanish(2001, close_price=1.1090, profit=15.0, close_reason="take_profit")
    tracker.check_positions()

    assert 2001 not in tracker._tracked, "should stop tracking once fully resolved"
    assert db._rows_by_trade_id["trade-abc"].real_status == "closed"
    assert db._rows_by_trade_id["trade-abc"].real_close_price == 1.1090


def test_check_positions_handles_history_cache_lag_without_losing_the_ticket():
    bridge = FakePositionBridge()
    bridge.add_open_position(2001, magic=900001)
    entry_time = datetime.datetime.now()
    db = FakeDB(rows=[FakeTradeRow("trade-abc", 2001, "open", entry_time)])
    tracker = PositionTracker(bridge, lambda: db, "user1", make_model_config())
    tracker.register_new_position(2001, "trade-abc", entry_time)

    bridge.simulate_vanish(2001, history_ready=False)
    tracker.check_positions()
    assert 2001 in tracker._tracked, "must not drop the ticket before history confirms the close"

    bridge._closed_history[2001] = {
        "ticket": 2001, "is_closed": True, "close_price": 1.1090,
        "close_time_utc": "x", "close_time_ny": "x", "profit": 15.0, "close_reason": "take_profit",
    }
    tracker.check_positions()
    assert 2001 not in tracker._tracked, "should finalize once history catches up"


def test_check_positions_fails_safe_on_bridge_error():
    class FailingBridge(FakePositionBridge):
        def get_positions(self, magic):
            raise Exception("simulated network failure")

    bridge = FailingBridge()
    tracker = PositionTracker(bridge, lambda: FakeDB(), "user1", make_model_config())
    tracker.register_new_position(2001, "trade-abc", datetime.datetime.now())

    tracker.check_positions()  # must not raise


def test_load_from_db_reconstructs_tracking_state():
    entry_time = datetime.datetime(2026, 8, 1, 9, 0)
    rows = [
        {"trade_id": "trade-open", "real_position_ticket": 3001, "real_status": "open", "entry_time_ny": entry_time, "direction": "long"},
        {"trade_id": "trade-partial", "real_position_ticket": 3002, "real_status": "partial_closed", "entry_time_ny": entry_time, "direction": "short"},
    ]

    class LoadFakeDB(FakeDB):
        def query(self, model_cls):
            return FakeQuery(rows)

    bridge = FakePositionBridge()
    tracker = PositionTracker(bridge, lambda: LoadFakeDB(), "user1", make_model_config())

    # get_open_real_trades() returns dicts directly (already shaped),
    # not ORM rows -- patch persistence.get_open_real_trades via a
    # simple monkeypatch of the module-level function it calls.
    import shadow_runner.position_tracker as pt_module
    original = pt_module.get_open_real_trades
    pt_module.get_open_real_trades = lambda db, user_id, model: rows
    try:
        tracker.load_from_db()
    finally:
        pt_module.get_open_real_trades = original

    assert 3001 in tracker._tracked
    assert tracker._tracked[3001]["partial_closed"] is False
    assert 3002 in tracker._tracked
    assert tracker._tracked[3002]["partial_closed"] is True
    assert tracker._tracked[3001]["trade_id"] == "trade-open"


def test_check_positions_failure_is_journaled_not_just_logged():
    """Reliability fix (this phase's chat history): PositionTracker
    uses its OWN DB-session-per-call path (unlike OrderManager, which
    piggybacks on a shared event_sink) -- worth proving directly that
    ITS failures are now journaled too, not just OrderManager's."""
    import shadow_runner.persistence as persistence_module

    written_events = []
    original_write_event = persistence_module.write_event

    def spy_write_event(db, event, user_id, model):
        written_events.append(event)
        return original_write_event(db, event, user_id, model)

    class FailingBridge(FakePositionBridge):
        def get_positions(self, magic):
            raise Exception("simulated network failure")

    bridge = FailingBridge()
    entry_time = datetime.datetime.now()
    db = FakeDB(rows=[FakeTradeRow("trade-abc", 2001, "open", entry_time)])
    tracker = PositionTracker(bridge, lambda: db, "user1", make_model_config())
    tracker.register_new_position(2001, "trade-abc", entry_time)

    import shadow_runner.position_tracker as pt_module
    pt_module.write_event = spy_write_event
    try:
        tracker.check_positions()  # must not raise
    finally:
        pt_module.write_event = original_write_event

    failure_events = [e for e in written_events if e.get("event_type") == "safety_check_failed"]
    assert len(failure_events) == 1
    assert failure_events[0]["check_name"] == "position_tracker_check_positions"


# ---------- check_for_orphans() -- continuous orphan-check, added 2026-09-04 ----------
#
# Real incident: a genuine orphan (a sibling-fill race from 2026-09-02)
# sat undetected for two days because the only existing orphan check ran
# solely at startup, after a detected cross-day gap -- never on an
# ordinary running day. check_for_orphaned_positions() itself already
# has thorough coverage in tests/shadow_runner/test_cross_day_recovery.py;
# these tests are specifically about the NEW behavior this method adds:
# throttling, and running even when self._tracked is empty.

def _patch_check_for_orphaned_positions(fake_fn):
    """Returns (patch, unpatch) for shadow_runner.position_tracker's
    imported reference -- same technique as this file's other tests
    patching pt_module.get_open_real_trades/write_event."""
    import shadow_runner.position_tracker as pt_module
    original = pt_module.check_for_orphaned_positions

    def patch():
        pt_module.check_for_orphaned_positions = fake_fn

    def unpatch():
        pt_module.check_for_orphaned_positions = original

    return patch, unpatch


def test_check_for_orphans_runs_even_when_nothing_is_tracked():
    """The single most important guarantee here: an orphan is, by
    definition, not yet in self._tracked -- gating this the same way
    check_positions() gates on self._tracked would make it permanently
    blind to the exact case it exists to catch."""
    calls = []

    def fake_check(bridge, symbol, magic, db, user_id, model, now_ny, event_sink):
        calls.append((symbol, magic, user_id, model))
        return []

    patch, unpatch = _patch_check_for_orphaned_positions(fake_check)
    bridge = FakePositionBridge()
    tracker = PositionTracker(bridge, lambda: FakeDB(), "user1", make_model_config())
    assert tracker._tracked == {}  # sanity check -- nothing tracked

    patch()
    try:
        tracker.check_for_orphans("EURUSDm")
    finally:
        unpatch()

    assert len(calls) == 1
    assert calls[0] == ("EURUSDm", 900001, "user1", "fvg")


def test_check_for_orphans_throttles_within_the_interval():
    calls = []

    def fake_check(bridge, symbol, magic, db, user_id, model, now_ny, event_sink):
        calls.append(now_ny)
        return []

    patch, unpatch = _patch_check_for_orphaned_positions(fake_check)
    bridge = FakePositionBridge()
    tracker = PositionTracker(bridge, lambda: FakeDB(), "user1", make_model_config())

    patch()
    try:
        tracker.check_for_orphans("EURUSDm")
        tracker.check_for_orphans("EURUSDm")  # immediately again -- must be a no-op
    finally:
        unpatch()

    assert len(calls) == 1


def test_check_for_orphans_runs_again_after_the_interval_elapses():
    calls = []

    def fake_check(bridge, symbol, magic, db, user_id, model, now_ny, event_sink):
        calls.append(now_ny)
        return []

    patch, unpatch = _patch_check_for_orphaned_positions(fake_check)
    bridge = FakePositionBridge()
    tracker = PositionTracker(bridge, lambda: FakeDB(), "user1", make_model_config())

    patch()
    try:
        tracker.check_for_orphans("EURUSDm")
        # Simulate enough real time having passed, rather than sleeping
        # in a test -- same technique as backdating timestamps elsewhere
        # in this file.
        tracker._last_orphan_check -= datetime.timedelta(minutes=6)
        tracker.check_for_orphans("EURUSDm")
    finally:
        unpatch()

    assert len(calls) == 2


def test_check_for_orphans_journals_found_events_and_commits():
    def fake_check(bridge, symbol, magic, db, user_id, model, now_ny, event_sink):
        event_sink({"event_type": "orphan_position_recovered", "timestamp": now_ny, "ticket": 999})
        return [{"ticket": 999, "healed": True}]

    patch, unpatch = _patch_check_for_orphaned_positions(fake_check)
    bridge = FakePositionBridge()
    db = FakeDB()
    tracker = PositionTracker(bridge, lambda: db, "user1", make_model_config())

    patch()
    try:
        tracker.check_for_orphans("EURUSDm")
    finally:
        unpatch()

    assert db.committed is True
    recovered = [e for e in db.added_events if e.event_type == "orphan_position_recovered"]
    assert len(recovered) == 1


def test_check_for_orphans_failure_is_journaled_not_just_logged():
    """DB errors from get_open_real_trades() inside
    check_for_orphaned_positions() aren't caught there -- this proves
    the belt-and-suspenders wrapper here catches them too, same
    reliability discipline as check_positions()'s own failure handling."""
    import shadow_runner.persistence as persistence_module

    written_events = []
    original_write_event = persistence_module.write_event

    def spy_write_event(db, event, user_id, model):
        written_events.append(event)
        return original_write_event(db, event, user_id, model)

    def fake_check(bridge, symbol, magic, db, user_id, model, now_ny, event_sink):
        raise Exception("simulated DB failure")

    check_patch, check_unpatch = _patch_check_for_orphaned_positions(fake_check)
    bridge = FakePositionBridge()
    db = FakeDB()
    tracker = PositionTracker(bridge, lambda: db, "user1", make_model_config())

    import shadow_runner.position_tracker as pt_module
    pt_module.write_event = spy_write_event
    check_patch()
    try:
        tracker.check_for_orphans("EURUSDm")  # must not raise
    finally:
        check_unpatch()
        pt_module.write_event = original_write_event

    failure_events = [e for e in written_events if e.get("event_type") == "safety_check_failed"]
    assert len(failure_events) == 1
    assert failure_events[0]["check_name"] == "continuous_orphan_check"