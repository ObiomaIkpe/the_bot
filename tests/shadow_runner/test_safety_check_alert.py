"""
Tests for ShadowRunner._write_events_now()'s safety_check_failed /
order_placement_failed -> Telegram alert hooks (logging/audit review
part 3, monitoring/alerting). send_telegram_alert() itself is
monkeypatched -- these tests only verify the hook fires for the right
event types, with the commit already having happened, not that
Telegram actually receives anything (see tests/app/test_telegram.py
for that).

2026-09-04 write-path audit: "which event types alert, and how" moved
out of _write_events_now() into the shared, centralized
app.core.telegram.alert_for_event() (see its own docstring for why --
this was the only alerting call site for a while, which is exactly how
PositionTracker's own writes ended up silently bypassing it entirely).
send_telegram_alert() itself is patched on app.core.telegram now, not
shadow_runner.runner -- that's where alert_for_event() actually calls
it from.
"""
import app.core.telegram as telegram_module
import shadow_runner.runner as runner_module
from shadow_runner.runner import ShadowRunner
from tests.shadow_runner.test_runner_orchestration import FakeDB, make_config


class FakeBridge:
    pass


def _make_runner(shared_writes):
    config = make_config()
    return ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: FakeDB(shared_writes))


def test_safety_check_failed_event_triggers_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(telegram_module, "send_telegram_alert", lambda text: alerts.append(text))

    runner = _make_runner([])
    runner._write_events_now([
        {
            "event_type": "safety_check_failed", "timestamp": "t",
            "check_name": "max_daily_loss", "error": "limit exceeded",
        },
    ])

    assert len(alerts) == 1
    assert "max_daily_loss" in alerts[0]
    assert "limit exceeded" in alerts[0]


def test_order_placement_failed_event_triggers_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(telegram_module, "send_telegram_alert", lambda text: alerts.append(text))

    runner = _make_runner([])
    runner._write_events_now([
        {
            "event_type": "order_placement_failed", "timestamp": "t",
            "candidate_key": "('long', 1.1)", "error": "bridge returned 503",
        },
    ])

    assert len(alerts) == 1
    assert "bridge returned 503" in alerts[0]


def test_orphan_events_now_also_trigger_alert(monkeypatch):
    """2026-09-04: added alongside centralizing alert_for_event() --
    before this, an orphan being found (even successfully healed and
    recorded) never alerted on EITHER of the two paths that write these
    events (this one, the rare startup/cross-day-gap path; or
    PositionTracker.check_for_orphans(), the common continuous one,
    fixed separately the same pass). Worth paging on regardless of
    whether the heal itself also failed -- finding an unmanaged real
    position at all is exactly what tonight's original incident was."""
    alerts = []
    monkeypatch.setattr(telegram_module, "send_telegram_alert", lambda text: alerts.append(text))

    runner = _make_runner([])
    runner._write_events_now([
        {"event_type": "orphan_position_recovered", "timestamp": "t", "ticket": 123, "target": 1.105},
        {"event_type": "orphan_trade_recorded", "timestamp": "t", "ticket": 123, "trade_id": "abc"},
    ])

    assert len(alerts) == 2
    assert "123" in alerts[0]
    assert "123" in alerts[1]


def test_other_event_types_do_not_trigger_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(telegram_module, "send_telegram_alert", lambda text: alerts.append(text))

    runner = _make_runner([])
    runner._write_events_now([
        {"event_type": "raid_detected", "timestamp": "t"},
        {"event_type": "order_filled", "timestamp": "t", "direction": "long", "entry": 1.1, "fill_bar_index": 0},
        # order_skipped_paused is an intentional, expected skip (model
        # paused) -- not a failure, deliberately not alerted on.
        {"event_type": "order_skipped_paused", "timestamp": "t"},
    ])

    assert alerts == []


def test_alert_fires_after_events_are_committed(monkeypatch):
    """The events must actually be journaled before the alert fires --
    an alert about something that failed to even get written would be
    worse than no alert (nothing to look at when someone checks)."""
    written = []
    alerts = []

    def fake_alert(text):
        # By the time this fires, the safety_check_failed row must
        # already be in shared_writes (i.e. committed, in the real DB
        # case) -- not just about to be.
        assert any(getattr(w, "event_type", None) == "safety_check_failed" for w in written)
        alerts.append(text)

    monkeypatch.setattr(telegram_module, "send_telegram_alert", fake_alert)

    runner = _make_runner(written)
    runner._write_events_now([
        {"event_type": "safety_check_failed", "timestamp": "t", "check_name": "x", "error": "y"},
    ])

    assert len(alerts) == 1
