"""
Phase 4 step 2c. Reacts to trade_candidate_ready events for ONE model
(FVG, OB, etc.) by placing real pending orders, tracking the race
between same-day candidates, cancelling the loser once one fills, and
attaching the computed take-profit once a real fill happens.

One OrderManager instance = one model, for one user, for one trading
day. A fresh instance is constructed each day (mirrors
DayOrchestrator's own one-instance-per-day lifecycle) -- there's no
cross-day state to carry, since "which candidate wins today" only ever
matters within a single day.

STATUS GATE: only 'active' models place real orders (checked via
is_active(), reading model_config["status"]). 'shadow' and 'disabled'
models can still receive on_trade_candidate_ready() calls harmlessly --
this makes it safe to wire an OrderManager for EVERY model_config row,
active or not, without an outer "if active" check at every call site.

RESOLVED (was an open item): position sizing now uses the symbol's real
contract specification, fetched from MT5 via the bridge's /symbol_info
endpoint (BridgeClient.get_symbol_info()), instead of an assumed
pip-value figure. See compute_lot_size()'s docstring for the exact math.
_compute_volume() fetches this once per OrderManager instance (cached --
contract specs don't change intraday) plus the account's current real
balance, and computes a genuine risk_pct-based lot size. Still worth a
one-time manual sanity check the first time a model actually goes
'active' -- compare the computed lot size's implied risk against the
account's real margin/balance in the MT5 terminal directly, per
PHASE4_BRIDGE_ORDERS.md's checklist -- but this is no longer a guess.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from shadow_runner.persistence import get_user_paused_status

NY_TZ = ZoneInfo("America/New_York")  # matches shadow_runner.runner's own convention --
                                        # every event timestamp elsewhere in this codebase
                                        # is NY wall-clock, not UTC (see persistence.py's
                                        # write_event() docstring)

log = logging.getLogger("shadow_runner.order_manager")

PIP = 0.0001
TARGET_LOOKBACK_BARS = 6  # MUST match phase1/streaming/trade_attempt.py's
                           # own TARGET_LOOKBACK_BARS constant exactly --
                           # this function replicates TradeAttempt's target
                           # calculation using real bars instead of
                           # simulated ones; a mismatch here would mean
                           # live targets silently diverge from what the
                           # validated model actually computes.


def compute_lot_size(
    balance: float, risk_pct: float, stop_distance_price: float, symbol_info: dict,
) -> float:
    """
    Real forex position sizing, using the symbol's ACTUAL contract
    specification (fetched via BridgeClient.get_symbol_info(), which
    calls MT5's own symbol_info() -- the broker's real, authoritative
    numbers for THIS specific symbol/account, not an assumed constant).

    This replaces an earlier version of this function that defaulted to
    an unverified "$10/pip per standard lot" figure -- see this
    module's OPEN ITEM history. That guess is gone; the math below uses
    only real values:

        pip_size = 0.0001  (standard forex pip -- matches PIP above and
            phase1/streaming/trade_attempt.py's own convention throughout
            this project)
        ticks_per_pip = pip_size / symbol_info["tick_size"]
        pip_value_per_lot = symbol_info["tick_value"] * ticks_per_pip
        risk_amount = balance * risk_pct
        stop_distance_pips = stop_distance_price / pip_size
        raw_lots = risk_amount / (stop_distance_pips * pip_value_per_lot)

    Rounded DOWN to the symbol's real volume_step (never up -- risk must
    never be inadvertently exceeded by rounding the wrong direction),
    floored at volume_min, capped at volume_max -- all three pulled from
    the same real symbol_info, not assumed.

    symbol_info: dict from BridgeClient.get_symbol_info() -- matches the
        bridge's SymbolInfoResponse field names exactly: tick_size,
        tick_value, volume_min, volume_max, volume_step.
    """
    if stop_distance_price <= 0:
        raise ValueError(f"stop_distance_price must be positive, got {stop_distance_price}")

    tick_size = symbol_info["tick_size"]
    tick_value = symbol_info["tick_value"]
    volume_step = symbol_info["volume_step"]
    volume_min = symbol_info["volume_min"]
    volume_max = symbol_info["volume_max"]

    ticks_per_pip = PIP / tick_size
    pip_value_per_lot = tick_value * ticks_per_pip

    stop_distance_pips = stop_distance_price / PIP
    risk_amount = balance * risk_pct
    raw_lots = risk_amount / (stop_distance_pips * pip_value_per_lot)

    steps = int((raw_lots / volume_step) + 1e-9)  # +epsilon guards against binary
                                                    # float imprecision (e.g. 0.1/0.01
                                                    # == 9.999999999999998, not 10.0,
                                                    # which would silently under-round
                                                    # without this) -- still always
                                                    # floors, never rounds UP past the
                                                    # true value
    lots = steps * volume_step
    lots = max(volume_min, lots)
    lots = min(volume_max, lots)
    return round(lots, 8)  # clean up float noise from the step multiplication


def compute_target(bars: list[dict], direction: str) -> float:
    """
    Replicates TradeAttempt's target calculation exactly, using real
    bars from the bridge instead of simulated ones: look at the
    TARGET_LOOKBACK_BARS bars STRICTLY BEFORE the fill (confirmed
    against extract_golden_master.py: highs[p-6:p] excludes index p,
    i.e. the fill bar itself is NOT part of the window), find the most
    extreme one (highest high for long, lowest low for short), target =
    that bar's (high+low)/2 midpoint.

    CALLER'S RESPONSIBILITY, not enforced here: `bars` must be exactly
    the window "before the fill" -- do not include the fill bar itself,
    and do not include any still-forming (not yet closed) bar. In live
    trading, a fill can happen at any moment (tick-level), not just at
    a bar close, so "before the fill" in practice means "the most
    recently CLOSED bars as of the moment the fill was detected" -- see
    runner.py's _check_order_manager_fills() for how those get fetched
    and filtered before this function ever sees them.

    bars: list of dicts with "high"/"low" keys, OLDEST FIRST, length
    >= TARGET_LOOKBACK_BARS.
    """
    if len(bars) < TARGET_LOOKBACK_BARS:
        raise ValueError(f"need at least {TARGET_LOOKBACK_BARS} bars, got {len(bars)}")
    window = bars[-TARGET_LOOKBACK_BARS:]
    if direction == "long":
        extreme = max(window, key=lambda b: b["high"])
    else:
        extreme = min(window, key=lambda b: b["low"])
    return (extreme["high"] + extreme["low"]) / 2


def build_comment(model_name: str, direction: str, raid_bar: int) -> str:
    """'FVG:long-12' format -- confirmed design (see this phase's chat
    history + mockup). Truncated defensively to MT5's 31-char comment
    cap -- shouldn't ever actually need truncating at realistic
    model-name lengths, but never trust an external system's limit
    silently."""
    return f"{model_name.upper()}:{direction}-{raid_bar}"[:31]


class OrderManager:
    def __init__(self, model_config: dict, symbol: str, bridge, session_factory, user_id: str, event_sink=None):
        """
        model_config: {"model_name": str, "status": "active"|"shadow"|"disabled",
                        "risk_pct": float, "magic_number": int}
        symbol: e.g. "EURUSDm"
        bridge: shadow_runner.bridge_client.BridgeClient
        session_factory: e.g. app.core.database.SessionLocal -- Phase 4
            step 4 addition, needed to check UserSettings.is_paused FRESH
            on every candidate (never cached -- see
            on_trade_candidate_ready()'s safety-rail check for why).
        user_id: whose UserSettings.is_paused to check.
        event_sink: optional callable(event: dict) -> None. Separate
            from DayOrchestrator's own event_sink -- these are
            order-manager-level events (order placed/cancelled/filled,
            target attached), not trading-logic events. New event types
            (see VALID_EVENT_TYPES additions needed in app/models/event.py):
            pending_order_placed, pending_order_cancelled,
            candidate_filled, target_attached, order_placement_failed,
            order_skipped_paused.
        """
        self.model_config = model_config
        self.symbol = symbol
        self.session_factory = session_factory
        self.user_id = user_id
        self.bridge = bridge
        self._emit = event_sink or (lambda e: None)

        self._pending = {}  # candidate_key -> {"order_ticket", "direction", "entry", "stop", "raid_bar"}
        self._winner_ticket = None
        self._winner_position_ticket = None
        self._closed_info = None  # None until check_for_close() detects and
                                    # confirms the real close -- Phase 4 step 3
        self._real_fill_price = None
        self._real_fill_time_utc = None
        self._real_fill_time_ny = None
        self._symbol_info_cache = None  # fetched lazily on first use, cached for
                                          # this instance's lifetime (one day) --
                                          # contract specs don't change intraday

    def is_active(self) -> bool:
        return self.model_config["status"] == "active"

    def _is_user_paused(self) -> bool:
        """Fresh DB read every call, deliberately not cached -- see
        __init__'s docstring. Fails toward NOT paused if the read itself
        fails (bridge is unrelated here, this is a direct DB call, so
        failure means a real DB problem) -- logged loudly rather than
        silently either way, since both "wrongly blocked" and "wrongly
        allowed" are real outcomes worth knowing about, but a DB
        connectivity problem shouldn't itself become a silent trading
        halt with no visibility into why."""
        db = self.session_factory()
        try:
            return get_user_paused_status(db, self.user_id)
        except Exception as e:
            log.error(
                "Could not check is_paused status (%s) -- proceeding as NOT paused. "
                "If this persists, investigate DB connectivity; a broken pause check "
                "should not itself become a silent, invisible trading halt.", e,
            )
            return False
        finally:
            db.close()

    def _now_ny(self):
        """Real wall-clock NY time -- used for events triggered by
        polling real broker state (fills, cancellations, target
        attachment), which don't have a natural bar timestamp to reuse
        the way on_trade_candidate_ready's events do."""
        return datetime.now(NY_TZ).replace(tzinfo=None)

    def on_trade_candidate_ready(self, event: dict) -> None:
        """Call from DayOrchestrator's event_sink whenever
        trade_candidate_ready fires for this model. Safe to call even
        when not active -- see module docstring's STATUS GATE note."""
        if not self.is_active():
            return
        if self._winner_ticket is not None:
            return  # today's race is already decided

        # Phase 4 step 4: account-wide emergency stop, checked FRESH
        # every single time (never cached) -- the whole point of
        # is_paused is being able to stop trading immediately without a
        # restart. Deliberately separate from ModelConfig.status (a
        # per-model, more deliberate switch) -- this is "stop
        # everything for this user right now."
        if self._is_user_paused():
            self._emit(
                {
                    "event_type": "order_skipped_paused",
                    "timestamp": event["timestamp"],
                    "direction": event["direction"],
                    "entry": event["entry"],
                }
            )
            return

        candidate_key = (event["raid_bar"], event["mss_bar"])
        if candidate_key in self._pending:
            return  # defensive -- shouldn't happen, DayOrchestrator emits once per candidate

        comment = build_comment(self.model_config["model_name"], event["direction"], event["raid_bar"])
        try:
            result = self.bridge.place_pending_order(
                symbol=self.symbol,
                direction=event["direction"],
                volume=self._compute_volume(event),
                entry_price=event["entry"],
                stop_loss=event["stop"],
                comment=comment,
                magic=self.model_config["magic_number"],
            )
        except Exception as e:
            log.error("Failed to place pending order for candidate %s: %s", candidate_key, e)
            self._emit(
                {"event_type": "order_placement_failed", "timestamp": event["timestamp"], "candidate_key": str(candidate_key), "error": str(e)}
            )
            return

        self._pending[candidate_key] = {
            "order_ticket": result["order_ticket"],
            "direction": event["direction"],
            "entry": event["entry"],
            "stop": event["stop"],
            "raid_bar": event["raid_bar"],
        }
        self._emit(
            {
                "event_type": "pending_order_placed",
                "timestamp": event["timestamp"],
                "order_ticket": result["order_ticket"],
                "direction": event["direction"],
                "entry": event["entry"],
                "stop": event["stop"],
            }
        )

    def _compute_volume(self, event: dict) -> float:
        """
        Real risk_pct-based position sizing (see compute_lot_size() and
        the module docstring's RESOLVED note). Falls back to the
        checklist's known-safe minimum (0.01) if either bridge call
        fails or the math itself errors -- a failure here should never
        crash order placement, but it also must never silently place a
        WRONG size; falling back to the smallest possible size is the
        safe direction to fail in, not falling back to something larger.
        """
        if self._symbol_info_cache is None:
            try:
                self._symbol_info_cache = self.bridge.get_symbol_info(self.symbol)
            except Exception as e:
                log.error(
                    "Could not fetch symbol_info for %s (%s) -- falling back to "
                    "minimum lot size 0.01 for this order", self.symbol, e,
                )
                return 0.01

        try:
            balance = self.bridge.account_info()["balance"]
        except Exception as e:
            log.error(
                "Could not fetch account balance (%s) -- falling back to minimum "
                "lot size 0.01 for this order", e,
            )
            return 0.01

        stop_distance = abs(event["entry"] - event["stop"])
        try:
            return compute_lot_size(
                balance, self.model_config["risk_pct"], stop_distance, self._symbol_info_cache
            )
        except Exception as e:
            # Broad catch deliberately: a malformed/missing symbol_info
            # response (e.g. None, or missing keys) raises TypeError/
            # KeyError, not just ValueError from a bad stop_distance --
            # the whole point of this fallback is to NEVER crash order
            # placement, whatever the failure mode.
            log.error(
                "compute_lot_size failed (%s) -- falling back to minimum lot size "
                "0.01 for this order", e,
            )
            return 0.01

    def check_for_fills(self) -> bool:
        """Call periodically (each poll cycle). Detects a candidate's
        pending order filling, cancels every sibling, and records the
        winner. Returns True if a fill was newly detected THIS call
        (caller should then fetch real closed bars and call
        attach_target()) -- False otherwise, including when there's
        nothing to check or a winner was already decided on a prior
        call. Handles at most one newly-detected fill per call -- keeps
        this simple and testable; a missed cycle just gets caught on
        the next poll."""
        if not self.is_active() or not self._pending or self._winner_ticket is not None:
            return False

        try:
            open_positions = self.bridge.get_positions(self.model_config["magic_number"])
            still_pending = self.bridge.get_pending_orders(self.model_config["magic_number"])
        except Exception as e:
            log.error("check_for_fills: bridge call failed, will retry next poll: %s", e)
            return False

        still_pending_tickets = {o["order_ticket"] for o in still_pending}

        for candidate_key, info in list(self._pending.items()):
            ticket = info["order_ticket"]
            if ticket in still_pending_tickets:
                continue
            matching_position = next((p for p in open_positions if p["ticket"] == ticket), None)
            if matching_position is None:
                log.warning(
                    "Candidate %s (ticket %s) vanished without a matching position -- "
                    "dropping (broker-side cancel/expiry we didn't initiate?)",
                    candidate_key, ticket,
                )
                del self._pending[candidate_key]
                continue
            self._on_fill(candidate_key, info, matching_position)
            return True
        return False

    def check_for_close(self) -> dict | None:
        """
        Phase 4 step 3. Call periodically (each poll cycle), same
        cadence as check_for_fills() -- but independent of it (this
        checks whether the WINNING position has since closed, not
        whether a candidate has filled). Returns the real close details
        (dict: close_price, profit, close_reason, close_time_utc/ny) the
        FIRST time a close is newly detected and confirmed -- None on
        every other call, including before there's a winner, after the
        close has already been recorded once, or if the position is
        still genuinely open.

        Two real broker-timing races handled deliberately, not just
        assumed away:
          1. A position can vanish from /positions the instant it
             closes, but MT5's OWN history cache can lag slightly
             before /history/position/{ticket} reflects it. If the
             history lookup says not-yet-closed despite the position
             having vanished, this method does NOT record anything --
             it logs a warning and waits for a future poll to retry,
             rather than recording a False negative.
          2. Bridge/network failures during either call fail safe: log
             and retry next poll, never crash, never record partial/
             wrong data.
        """
        if self._winner_position_ticket is None or self._closed_info is not None:
            return None

        try:
            open_positions = self.bridge.get_positions(self.model_config["magic_number"])
        except Exception as e:
            log.error("check_for_close: bridge call failed, will retry next poll: %s", e)
            return None

        still_open = any(p["ticket"] == self._winner_position_ticket for p in open_positions)
        if still_open:
            return None

        try:
            history = self.bridge.get_position_history(self._winner_position_ticket)
        except Exception as e:
            log.error(
                "check_for_close: could not fetch close history for ticket %s: %s -- will retry next poll",
                self._winner_position_ticket, e,
            )
            return None

        if not history.get("is_closed"):
            log.warning(
                "Ticket %s vanished from open positions but history shows not yet "
                "closed -- likely a brief broker-side history-cache lag; will retry next poll",
                self._winner_position_ticket,
            )
            return None

        self._closed_info = history
        self._emit(
            {
                "event_type": "real_trade_closed",
                "timestamp": self._now_ny(),
                "ticket": self._winner_position_ticket,
                "close_price": history["close_price"],
                "profit": history["profit"],
                "close_reason": history["close_reason"],
            }
        )
        return history

    def get_real_outcome(self) -> dict | None:
        """
        Phase 4 step 3 (part 2). Called by the runner once a day
        finalizes, to correlate this model's real order/fill/close data
        into the SAME trade row _write_trade() already writes for the
        simulated outcome (see runner.py's _write_trade()).

        Returns None if no candidate ever filled today (nothing real to
        report). Otherwise returns a dict with fill data always present,
        and close data present only if check_for_close() had already
        confirmed it by the time this is called -- a real trade can
        still be genuinely open at day finalize time (day_end doesn't
        force-close real positions the way the simulation's finalize()
        force-closes its own simulated attempt; see this module's
        cancel_all_at_day_end(), which only cancels UNFILLED pending
        orders, never touches an already-open real position). In that
        case close_* fields are None -- the trade row gets the fill data
        now and stays open on the real-outcome side until a later day's
        poll cycle (via a still-running OrderManager -- NOT YET BUILT
        for cross-day continuation, see this method's caller for the
        current one-day-only limitation) eventually detects the close.
        """
        if self._winner_position_ticket is None:
            return None
        return {
            "position_ticket": self._winner_position_ticket,
            "fill_price": self._real_fill_price,
            "fill_time_utc": self._real_fill_time_utc,
            "fill_time_ny": self._real_fill_time_ny,
            "close_price": self._closed_info["close_price"] if self._closed_info else None,
            "close_time_utc": self._closed_info["close_time_utc"] if self._closed_info else None,
            "close_time_ny": self._closed_info["close_time_ny"] if self._closed_info else None,
            "profit": self._closed_info["profit"] if self._closed_info else None,
            "close_reason": self._closed_info["close_reason"] if self._closed_info else None,
        }

    def _on_fill(self, winning_key: tuple, winning_info: dict, position: dict) -> None:
        self._winner_ticket = winning_info["order_ticket"]
        self._winner_position_ticket = position["ticket"]
        # Stored for get_real_outcome() -- see this method's docstring.
        # position["time_utc"]/["time_ny"] are the REAL broker fill
        # timestamps (strings, ISO format, as returned by the bridge's
        # Position model) -- distinct from winning_info's entry/stop,
        # which are the CANDIDATE's intended values, not what actually
        # happened.
        self._real_fill_price = position["open_price"]
        self._real_fill_time_utc = position["time_utc"]
        self._real_fill_time_ny = position["time_ny"]
        self._emit(
            {
                "event_type": "candidate_filled",
                "timestamp": self._now_ny(),
                "order_ticket": position["ticket"],
                "direction": winning_info["direction"],
                "fill_price": position["open_price"],
            }
        )

        for candidate_key, info in list(self._pending.items()):
            if candidate_key == winning_key:
                continue
            try:
                self.bridge.cancel_pending_order(info["order_ticket"])
                self._emit(
                    {
                        "event_type": "pending_order_cancelled",
                        "timestamp": self._now_ny(),
                        "order_ticket": info["order_ticket"],
                        "reason": "sibling_filled",
                    }
                )
            except Exception as e:
                log.error("Failed to cancel sibling order %s: %s", info["order_ticket"], e)

        self._pending = {winning_key: winning_info}

    def attach_target(self, recent_bars: list[dict]) -> None:
        """Call once, after check_for_fills() has detected a fill, with
        real recent bars from the bridge (oldest-first, ending at/after
        the fill bar). Computes the target the same way TradeAttempt
        does and attaches it via modify_position(). A separate method
        (not called automatically) so the caller controls exactly when
        and how bars get fetched."""
        if self._winner_position_ticket is None or not self._pending:
            return
        winning_info = next(iter(self._pending.values()))
        try:
            target = compute_target(recent_bars, winning_info["direction"])
        except ValueError as e:
            log.error("Could not compute target (insufficient bars): %s -- leaving take_profit unset", e)
            return
        try:
            self.bridge.modify_position(self._winner_position_ticket, take_profit=target)
            self._emit(
                {"event_type": "target_attached", "timestamp": self._now_ny(), "ticket": self._winner_position_ticket, "target": target}
            )
        except Exception as e:
            log.error("Failed to attach target to position %s: %s", self._winner_position_ticket, e)

    def cancel_all_at_day_end(self) -> None:
        """Call once, at day_end (5pm NY) -- cancels anything still
        genuinely pending (never filled). The winner's own order (now a
        real position, not a pending order) is correctly skipped."""
        for candidate_key, info in list(self._pending.items()):
            if info["order_ticket"] == self._winner_ticket:
                continue
            try:
                self.bridge.cancel_pending_order(info["order_ticket"])
                self._emit(
                    {
                        "event_type": "pending_order_cancelled",
                        "timestamp": self._now_ny(),
                        "order_ticket": info["order_ticket"],
                        "reason": "day_end",
                    }
                )
            except Exception as e:
                log.error("Failed to cancel end-of-day order %s: %s", info["order_ticket"], e)
        self._pending = {}