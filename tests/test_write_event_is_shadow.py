"""
Tests for the is_shadow fix in shadow_runner.persistence.write_event().

Was hardcoded True on every Event row, unconditionally -- correct back
when only DayOrchestrator/DaySelectionGate ever emitted events (Phase
3), silently wrong once OrderManager started emitting real-action
events (Phase 4). Now derived from event_type via
app.models.event.REAL_ACTION_EVENT_TYPES.
"""
from shadow_runner.persistence import write_event


class RecordingFakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_simulation_events_are_shadow():
    """DayOrchestrator/DaySelectionGate-style events always describe
    detection/simulation logic -- never a real action, regardless of
    model status."""
    db = RecordingFakeDB()
    for event_type in [
        "raid_detected", "mss_confirmed", "fvg_found", "trade_closed",
        "day_trend_determined", "day_skipped_no_trend", "trade_candidate_ready",
        "fvg_rejected_min_stop", "daily_swing_high_confirmed",
    ]:
        write_event(db, {"event_type": event_type, "timestamp": "t"}, "user1", "fvg")

    assert all(row.is_shadow is True for row in db.added), (
        f"expected all simulation events to be is_shadow=True, got: "
        f"{[(r.event_type, r.is_shadow) for r in db.added]}"
    )


def test_order_manager_events_are_not_shadow():
    """OrderManager only ever emits its own events when is_active() is
    True -- so every event type it produces is, by construction, always
    a real action, never merely simulated."""
    db = RecordingFakeDB()
    for event_type in [
        "pending_order_placed", "pending_order_cancelled", "candidate_filled",
        "target_attached", "order_placement_failed", "order_skipped_paused",
        "real_trade_closed", "partial_close_executed", "duplicate_fill_closed",
        "orphan_position_recovered",
    ]:
        write_event(db, {"event_type": event_type, "timestamp": "t"}, "user1", "fvg")

    assert all(row.is_shadow is False for row in db.added), (
        f"expected all order-manager events to be is_shadow=False, got: "
        f"{[(r.event_type, r.is_shadow) for r in db.added]}"
    )


def test_unrecognized_event_type_defaults_to_shadow_the_safe_direction():
    db = RecordingFakeDB()
    write_event(db, {"event_type": "some_future_event_nobody_categorized_yet", "timestamp": "t"}, "user1", "fvg")
    assert db.added[0].is_shadow is True


def test_write_event_still_correctly_splits_details_regardless_of_is_shadow():
    """Confirm the is_shadow fix didn't disturb the existing, already-
    correct event_type/timestamp/details splitting behavior."""
    db = RecordingFakeDB()
    write_event(
        db,
        {"event_type": "pending_order_placed", "timestamp": "t", "direction": "long", "entry": 1.105},
        "user1", "fvg",
    )
    row = db.added[0]
    assert row.event_type == "pending_order_placed"
    assert row.timestamp == "t"
    assert row.details == {"direction": "long", "entry": 1.105}
    assert row.is_shadow is False