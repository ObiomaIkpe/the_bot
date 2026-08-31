"""
Dead-man's-switch heartbeat pings to healthchecks.io (logging/audit
review part 3 -- monitoring/alerting, "process/service down" trigger).

Each monitored service (api, shadow_runner) pings its OWN check URL on
a schedule; healthchecks.io itself sends the alert if a ping doesn't
arrive within the check's configured period+grace. This is deliberately
NOT a same-VPS watcher polling a /health endpoint -- a watcher living
on the same box as the thing it's watching dies with it if the whole
VPS goes down (power loss, OOM, network outage), and that's exactly the
"silent death" scenario that matters most here. healthchecks.io itself
is external, so it still pages in that case.

Dormant by default: no-ops (with one warning log, not per-call) if
HEALTHCHECKS_PING_URL isn't set, same pattern as app.core.telegram.
"""
import logging
import time

import requests

from app.core.config import settings

log = logging.getLogger("app.core.healthchecks")

_warned_unconfigured = False


def ping_healthchecks() -> None:
    """A single ping. Deliberately fire-and-forget: any failure here
    (network hiccup, healthchecks.io itself down) is caught and logged,
    never raised -- a missed ping is exactly what the dead-man's-switch
    is FOR (healthchecks.io pages on a stale check either way), and this
    must never be able to crash the loop it's trying to prove is alive."""
    global _warned_unconfigured
    if not settings.healthchecks_ping_url:
        if not _warned_unconfigured:
            log.warning("HEALTHCHECKS_PING_URL not set -- heartbeat pings disabled")
            _warned_unconfigured = True
        return
    try:
        requests.get(settings.healthchecks_ping_url, timeout=5)
    except Exception:
        log.warning("Failed to ping healthchecks.io", exc_info=True)


class HeartbeatPinger:
    """Wraps ping_healthchecks() with a minimum interval between actual
    network calls, so a call site can invoke maybe_ping() unconditionally
    on every loop iteration regardless of how tight that loop's own
    cadence is (shadow_runner's poll_interval_seconds can be much
    shorter than any sane heartbeat period) without hammering
    healthchecks.io or generating pointless log noise."""

    def __init__(self, min_interval_seconds: int = 60):
        self._min_interval = min_interval_seconds
        self._last_ping = 0.0

    def maybe_ping(self) -> None:
        now = time.monotonic()
        if now - self._last_ping >= self._min_interval:
            ping_healthchecks()
            self._last_ping = now
