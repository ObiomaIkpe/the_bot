"""
Confirms run_forever() pings the heartbeat every loop iteration
(logging/audit review part 3, monitoring/alerting) -- regardless of
whether poll_once() succeeded, raised BridgeError, or raised anything
else, since a caught error still proves the loop itself is alive and
cycling. Breaks out of the otherwise-infinite loop via a fake sleep
that raises after N iterations -- run_forever() itself is never
directly tested elsewhere for the same "infinite loop" reason.
"""
import shadow_runner.runner as runner_module
from shadow_runner.runner import ShadowRunner
from tests.shadow_runner.test_runner_orchestration import FakeDB, make_config


class FakeBridge:
    pass


class StopTheLoop(Exception):
    pass


def _make_runner():
    config = make_config()
    return ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: FakeDB([]))


def test_heartbeat_pinged_every_iteration_even_after_bridge_error(monkeypatch):
    ping_calls = []
    monkeypatch.setattr(
        runner_module.HeartbeatPinger, "maybe_ping", lambda self: ping_calls.append(1),
    )

    poll_calls = []

    def fake_poll_once(self):
        poll_calls.append(1)
        raise runner_module.BridgeError("bridge down this poll")

    monkeypatch.setattr(ShadowRunner, "poll_once", fake_poll_once)

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise StopTheLoop()

    monkeypatch.setattr(runner_module.time_module, "sleep", fake_sleep)

    runner = _make_runner()
    try:
        runner.run_forever()
    except StopTheLoop:
        pass

    assert len(poll_calls) == 3
    assert len(ping_calls) == 3, "heartbeat must ping every iteration, even after a caught BridgeError"
