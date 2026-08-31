"""
Tests for app.core.healthchecks -- logging/audit review part 3
(monitoring/alerting, "process/service down" trigger). Never hits the
real healthchecks.io API; requests.get is monkeypatched throughout.
"""
import app.core.healthchecks as healthchecks_module
from app.core.config import settings
from app.core.healthchecks import HeartbeatPinger, ping_healthchecks


def test_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "healthchecks_ping_url", None)
    calls = []
    monkeypatch.setattr(healthchecks_module.requests, "get", lambda *a, **kw: calls.append((a, kw)))

    ping_healthchecks()
    assert calls == []


def test_pings_configured_url(monkeypatch):
    monkeypatch.setattr(settings, "healthchecks_ping_url", "https://hc-ping.com/some-uuid")
    calls = []
    monkeypatch.setattr(healthchecks_module.requests, "get", lambda url, timeout: calls.append((url, timeout)))

    ping_healthchecks()

    assert calls == [("https://hc-ping.com/some-uuid", 5)]


def test_ping_exception_is_caught_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "healthchecks_ping_url", "https://hc-ping.com/some-uuid")

    def raising_get(*a, **kw):
        raise ConnectionError("network is down")

    monkeypatch.setattr(healthchecks_module.requests, "get", raising_get)

    ping_healthchecks()  # must not raise


def test_heartbeat_pinger_throttles_to_min_interval(monkeypatch):
    monkeypatch.setattr(settings, "healthchecks_ping_url", "https://hc-ping.com/some-uuid")
    calls = []
    monkeypatch.setattr(healthchecks_module.requests, "get", lambda *a, **kw: calls.append(1))

    fake_now = [1000.0]
    monkeypatch.setattr(healthchecks_module.time, "monotonic", lambda: fake_now[0])

    pinger = HeartbeatPinger(min_interval_seconds=60)
    pinger.maybe_ping()  # first call always pings
    assert len(calls) == 1

    fake_now[0] += 10  # well within the 60s window
    pinger.maybe_ping()
    assert len(calls) == 1, "must not ping again before the interval elapses"

    fake_now[0] += 55  # now 65s since the first ping
    pinger.maybe_ping()
    assert len(calls) == 2
