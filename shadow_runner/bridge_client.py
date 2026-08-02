"""
Thin HTTP client for the Phase 2 MT5 bridge. Read-only, matches the
bridge's actual endpoints exactly (see bridge/README.md /
PHASE2_VALIDATION.md) -- no order functions exist on the bridge side to
call even if this file wanted to.
"""
import logging
from datetime import datetime

import requests

log = logging.getLogger("shadow_runner.bridge_client")


class BridgeError(Exception):
    """Raised on any bridge call failure (network error, non-200, or
    malformed response)."""


class BridgeClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict:
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=self.timeout_seconds)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /health failed: {e}") from e

    def account_info(self) -> dict:
        try:
            resp = requests.get(f"{self.base_url}/account_info", timeout=self.timeout_seconds)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /account_info failed: {e}") from e

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[dict]:
        """
        Returns bars in the same order the bridge does (oldest first --
        confirmed in PHASE2_VALIDATION.md's sample output). Each bar dict
        has time_utc/time_ny (both parsed to tz-aware datetimes here,
        strings on the wire) plus open/high/low/close/tick_volume/spread/
        real_volume, matching the bridge's CandlesResponse model exactly.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/candles",
                params={"symbol": symbol, "timeframe": timeframe, "count": count},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /candles failed: {e}") from e

        candles = []
        for c in data.get("candles", []):
            try:
                candles.append(
                    {
                        **c,
                        "time_utc": datetime.fromisoformat(c["time_utc"]),
                        "time_ny": datetime.fromisoformat(c["time_ny"]),
                    }
                )
            except (KeyError, ValueError) as e:
                raise BridgeError(f"Malformed candle in bridge response: {c!r} ({e})") from e
        return candles