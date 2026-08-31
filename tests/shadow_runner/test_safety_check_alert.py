"""
Tests for ShadowRunner._write_events_now()'s safety_check_failed ->
Telegram alert hook (logging/audit review part 3, monitoring/alerting).
send_telegram_alert() itself is monkeypatched -- these tests only
verify the hook fires for the right event type, with the commit
already having happened, not that Telegram actually receives anything
(see tests/app/test_telegram.py for that).
"""
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
    monkeypatch.setattr(runner_module, "send_telegram_alert", lambda text: alerts.append(text))

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


def test_other_event_types_do_not_trigger_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(runner_module, "send_telegram_alert", lambda text: alerts.append(text))

    runner = _make_runner([])
    runner._write_events_now([
        {"event_type": "raid_detected", "timestamp": "t"},
        {"event_type": "order_filled", "timestamp": "t", "direction": "long", "entry": 1.1, "fill_bar_index": 0},
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

    monkeypatch.setattr(runner_module, "send_telegram_alert", fake_alert)

    runner = _make_runner(written)
    runner._write_events_now([
        {"event_type": "safety_check_failed", "timestamp": "t", "check_name": "x", "error": "y"},
    ])

    assert len(alerts) == 1
