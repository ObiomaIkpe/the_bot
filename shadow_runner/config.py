"""
Shadow runner configuration. Everything identity/environment-specific
comes from env vars, same principle as app/core/config.py -- never
hardcoded.
"""
import os


class ShadowRunnerConfig:
    def __init__(self):
        self.bridge_url = os.environ["BRIDGE_URL"].rstrip("/")
        # e.g. http://38.247.137.208:8001 -- the Phase 2 bridge on the VPS.
        # No default: this must be set explicitly, never silently guessed.

        self.user_id = os.environ["SHADOW_RUNNER_USER_ID"]
        # The real registered user this runner journals events/trades
        # against. Must already exist in `users`, with a matching
        # `user_settings` row (risk_pct is read from there, not here).

        self.symbol = os.environ.get("SHADOW_RUNNER_SYMBOL", "EURUSDm")
        # Matches the bridge's confirmed symbol (Phase 2 validation).

        self.model = "fvg"
        # Hardcoded, deliberately: this is the only model with a working
        # streaming pipeline (Phase 1 only reimplemented FVG). Not an env
        # var, because there is currently nothing else it could correctly
        # be -- OB and FVG+OB have no streaming implementation yet. See
        # HANDOFF.md.

        self.poll_interval_seconds = int(os.environ.get("SHADOW_RUNNER_POLL_SECONDS", "60"))
        # How often to check the bridge for newly-closed bars. Bounded
        # below by the bridge's own rate limits (none currently enforced,
        # but be a reasonable citizen) and above by how much journaling
        # latency is acceptable -- see day_state.py's docstring for the
        # unrelated ~10am journaling-latency tradeoff, which this setting
        # does not affect.

        self.candles_fetch_count = int(os.environ.get("SHADOW_RUNNER_FETCH_COUNT", "20"))
        # How many recent M5 candles to request each poll. Generous
        # buffer so a missed poll cycle (network blip, brief bridge
        # downtime) doesn't lose bars -- see bridge_client.py.