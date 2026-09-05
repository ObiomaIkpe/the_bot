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

    def get_candles(self, symbol: str, timeframe: str, count: int, start_pos: int = 0) -> list[dict]:
        """
        Returns bars in the same order the bridge does (oldest first --
        confirmed in PHASE2_VALIDATION.md's sample output). Each bar dict
        has time_utc/time_ny (both parsed to tz-aware datetimes here,
        strings on the wire) plus open/high/low/close/tick_volume/spread/
        real_volume, matching the bridge's CandlesResponse model exactly.

        start_pos (2026-09-04, historical backfill): 0 = most recent bar
        (unchanged default -- every existing caller is unaffected). A
        non-zero value pages further back than one 5000-bar call can
        reach -- see get_candles_paginated() below, which is what should
        actually be used for a deep historical fetch; this method still
        only ever returns one page.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/candles",
                params={"symbol": symbol, "timeframe": timeframe, "count": count, "start_pos": start_pos},
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

    # Bridge-enforced ceiling on a single /candles call (main.py's
    # `count: int = Query(..., le=5000)`) -- kept here as the page size
    # get_candles_paginated pages by, not just a magic number.
    MAX_CANDLES_PER_CALL = 5000
    # Sanity bound so a bug (e.g. a broker that never returns a short
    # final page) can't spin pulling the entire available terminal
    # history -- 5 pages * 5000 = 25000 M5 bars, comfortably more than
    # the ~7000-7200 bars a 25-calendar-day backfill actually needs.
    MAX_PAGES = 5

    def get_candles_paginated(self, symbol: str, timeframe: str, total_bars_needed: int) -> list[dict]:
        """
        2026-09-04, historical backfill (Aug 10 -> Sept 4 window):
        get_candles() is capped at MAX_CANDLES_PER_CALL bars per call,
        always anchored at the most recent bar -- not enough to reach a
        gap wider than ~17 trading days. Pages backward via start_pos
        (0, 5000, 10000, ...) until total_bars_needed bars are collected
        or a page comes back shorter than requested (terminal history
        exhausted -- MT5 has nothing older to give), capped at MAX_PAGES
        pages as a safety bound.

        Each page is itself oldest-first (see get_candles()'s docstring),
        but an EARLIER page (lower start_pos) is always the MORE RECENT
        segment -- so pages are collected then explicitly re-sorted by
        time_utc (also de-duplicated by time_utc) rather than trusting
        any assumed concatenation order between pages. Self-corrects even
        if the exact MT5 boundary behavior between pages turns out to
        overlap by a bar or two.
        """
        all_bars: dict[datetime, dict] = {}
        start_pos = 0
        for _ in range(self.MAX_PAGES):
            page = self.get_candles(symbol, timeframe, self.MAX_CANDLES_PER_CALL, start_pos=start_pos)
            for bar in page:
                all_bars[bar["time_utc"]] = bar
            if len(all_bars) >= total_bars_needed or len(page) < self.MAX_CANDLES_PER_CALL:
                break
            start_pos += self.MAX_CANDLES_PER_CALL
        return sorted(all_bars.values(), key=lambda b: b["time_utc"])

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
                # BUG FIX (found while building the trade-lifecycle proof
                # script): only_ours=True resolves server-side to the
                # BRIDGE WORKER's single configured config.magic_number
                # (900001) -- NOT the magic passed into this method. Any
                # caller asking for a different magic (a second model, or
                # this proof script's 999999 test magic) would always get
                # an empty list back, silently, even with a real matching
                # order on the account. only_ours=False returns every
                # order regardless of magic; the explicit filter below is
                # what actually narrows it to the magic we want.
                params={"only_ours": False},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            all_orders = resp.json().get("orders", [])
        except requests.RequestException as e:
            raise BridgeError(f"GET /orders/pending failed: {e}") from e
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
                # BUG FIX -- see the matching note in get_pending_orders()
                # above: only_ours=True filters server-side to the bridge
                # worker's own config.magic_number (900001), regardless of
                # the magic argument passed here. only_ours=False returns
                # every position; we filter explicitly by magic below.
                params={"only_ours": False},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            all_positions = resp.json().get("positions", [])
        except requests.RequestException as e:
            raise BridgeError(f"GET /positions failed: {e}") from e
        return [p for p in all_positions if p.get("magic") == magic]

    def close_position(self, ticket: int) -> dict:
        """Full close of an open position. The bridge itself has always
        had POST /positions/{ticket}/close (see bridge/app/main.py) --
        this client just never wrapped it; only close_position_partial
        existed here before. Added while building the trade-lifecycle
        proof script, which needs a real full close for teardown."""
        try:
            resp = requests.post(
                f"{self.base_url}/positions/{ticket}/close", timeout=self.timeout_seconds
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise BridgeError(f"POST /positions/{ticket}/close failed: {detail}") from e

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

    def get_deals_history(self, date_from: datetime, date_to: datetime) -> list[dict]:
        """
        2026-09-04, historical reconciliation Piece B. Every deal across
        the account in [date_from, date_to] -- unlike get_position_history()
        above, not scoped to one known ticket. Deliberately unfiltered by
        symbol/magic (the bridge's own philosophy, see mt5_client.py) --
        the caller filters. Same isoformat()-on-the-way-in,
        fromisoformat()-on-the-way-out pattern as get_candles().
        """
        try:
            resp = requests.get(
                f"{self.base_url}/history/deals",
                params={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise BridgeError(f"GET /history/deals failed: {e}") from e

        deals = []
        for d in data.get("deals", []):
            try:
                deals.append({
                    **d,
                    "time_utc": datetime.fromisoformat(d["time_utc"]),
                    "time_ny": datetime.fromisoformat(d["time_ny"]),
                })
            except (KeyError, ValueError) as e:
                raise BridgeError(f"Malformed deal in bridge response: {d!r} ({e})") from e
        return deals

    def close_position_partial(self, ticket: int, volume: float) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}/positions/{ticket}/close_partial",
                json={"volume": volume},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise BridgeError(f"POST /positions/{ticket}/close_partial failed: {detail}") from e