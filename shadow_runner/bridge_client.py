"""
Thin HTTP client for the Phase 2/4 MT5 bridge. Matches the bridge's
actual endpoints exactly (see bridge/README.md / PHASE2_VALIDATION.md /
PHASE4_BRIDGE_ORDERS.md).

Order-placing methods below (place_pending_order, cancel_pending_order,
modify_position, get_positions) call endpoints gated behind the bridge's
own orders_enabled config -- this client doesn't add any additional
safety layer of its own beyond what the bridge already enforces. Every
order-placing call also accepts an explicit magic number (Phase 4
multi-model addition) -- callers must always pass the calling model's
own magic_number from its ModelConfig row, never rely on the bridge's
worker-level default, since multiple models share one bridge worker.
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

    # -----------------------------------------------------------------
    # Phase 4: order placement
    # -----------------------------------------------------------------

    def place_pending_order(
        self, symbol: str, direction: str, volume: float,
        entry_price: float, stop_loss: float, comment: str, magic: int,
    ) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}/orders/pending",
                json={
                    "symbol": symbol, "direction": direction, "volume": volume,
                    "entry_price": entry_price, "stop_loss": stop_loss,
                    "comment": comment, "magic": magic,
                },
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise BridgeError(f"POST /orders/pending failed: {detail}") from e

    def get_pending_orders(self, magic: int) -> list[dict]:
        try:
            resp = requests.get(
                f"{self.base_url}/orders/pending",
                params={"only_ours": True},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            all_orders = resp.json().get("orders", [])
        except requests.RequestException as e:
            raise BridgeError(f"GET /orders/pending failed: {e}") from e
        # Belt-and-suspenders filter: only_ours=true already filters to
        # the BRIDGE WORKER's default magic, which may not match this
        # specific model's magic if multiple models share the worker --
        # filter explicitly by the exact magic passed in.
        return [o for o in all_orders if o.get("magic") == magic]

    def cancel_pending_order(self, ticket: int) -> dict:
        try:
            resp = requests.delete(
                f"{self.base_url}/orders/pending/{ticket}", timeout=self.timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise BridgeError(f"DELETE /orders/pending/{ticket} failed: {detail}") from e

    def get_positions(self, magic: int) -> list[dict]:
        try:
            resp = requests.get(
                f"{self.base_url}/positions",
                params={"only_ours": True},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            all_positions = resp.json().get("positions", [])
        except requests.RequestException as e:
            raise BridgeError(f"GET /positions failed: {e}") from e
        return [p for p in all_positions if p.get("magic") == magic]

    def modify_position(self, ticket: int, take_profit: float) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}/positions/{ticket}/modify",
                json={"take_profit": take_profit},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise BridgeError(f"POST /positions/{ticket}/modify failed: {detail}") from e

    def get_symbol_info(self, symbol: str) -> dict:
        try:
            resp = requests.get(
                f"{self.base_url}/symbol_info",
                params={"symbol": symbol},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /symbol_info failed: {e}") from e

    def get_position_history(self, ticket: int) -> dict:
        try:
            resp = requests.get(
                f"{self.base_url}/history/position/{ticket}", timeout=self.timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /history/position/{ticket} failed: {e}") from e

    def get_symbol_info(self, symbol: str) -> dict:
        """The REAL contract spec for this symbol -- see
        order_manager.py's compute_lot_size() for why this replaced an
        assumed pip-value figure."""
        try:
            resp = requests.get(
                f"{self.base_url}/symbol_info", params={"symbol": symbol}, timeout=self.timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /symbol_info failed: {e}") from e