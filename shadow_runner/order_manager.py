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

OPEN ITEM, DELIBERATELY NOT SOLVED HERE: _compute_volume() is a
documented placeholder returning the checklist's known-safe minimum lot
size (0.01), NOT a real risk_pct-based position size. Real position
sizing needs (a) the account's current real balance (from the bridge's
/account_info) and (b) a confirmed pip-value-per-lot figure for
EURUSDm's specific contract on Exness (the 10.0 USD/pip/standard-lot
default in compute_lot_size() below is the conventional EURUSD value,
NOT yet verified against Exness's actual contract spec -- micro/cent
account variants sometimes differ). Do not flip any model to 'active'
in a way that actually risks money until this is resolved for real.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

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
    balance: float, risk_pct: float, stop_distance_price: float,
    pip_value_per_pip_per_lot: float = 10.0,
) -> float:
    """
    Standard forex position sizing:
        risk_amount = balance * risk_pct
        lot_size = risk_amount / (stop_distance_in_pips * pip_value_per_lot)

    pip_value_per_pip_per_lot defaults to $10/pip per 1.0 (standard) lot
    -- the conventional EURUSD value for a USD-denominated account.
    UNVERIFIED against Exness's actual EURUSDm contract specification --
    see this module's docstring. Do not trust this default for real
    position sizing without confirming it first (check a real trade's
    margin/pip-value directly in the MT5 terminal).

    Rounds DOWN to 2 decimal places (MT5's typical minimum lot step) --
    always down, never up, so risk is never inadvertently exceeded by
    rounding in the wrong direction. Floors at 0.01 (can't place smaller).
    """
    if stop_distance_price <= 0:
        raise ValueError(f"stop_distance_price must be positive, got {stop_distance_price}")
    stop_distance_pips = stop_distance_price / PIP
    risk_amount = balance * risk_pct
    raw_lots = risk_amount / (stop_distance_pips * pip_value_per_pip_per_lot)
    return max(0.01, int(raw_lots * 100) / 100)


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
    def __init__(self, model_config: dict, symbol: str, bridge, event_sink=None):
        """
        model_config: {"model_name": str, "status": "active"|"shadow"|"disabled",
                        "risk_pct": float, "magic_number": int}
        symbol: e.g. "EURUSDm"
        bridge: shadow_runner.bridge_client.BridgeClient
        event_sink: optional callable(event: dict) -> None. Separate
            from DayOrchestrator's own event_sink -- these are
            order-manager-level events (order placed/cancelled/filled,
            target attached), not trading-logic events. New event types
            (see VALID_EVENT_TYPES additions needed in app/models/event.py):
            pending_order_placed, pending_order_cancelled,
            candidate_filled, target_attached, order_placement_failed.
        """
        self.model_config = model_config
        self.symbol = symbol
        self.bridge = bridge
        self._emit = event_sink or (lambda e: None)

        self._pending = {}  # candidate_key -> {"order_ticket", "direction", "entry", "stop", "raid_bar"}
        self._winner_ticket = None
        self._winner_position_ticket = None

    def is_active(self) -> bool:
        return self.model_config["status"] == "active"

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
        # PLACEHOLDER -- see module docstring's OPEN ITEM. Returns the
        # known-safe minimum, NOT a real risk_pct-based size.
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

    def _on_fill(self, winning_key: tuple, winning_info: dict, position: dict) -> None:
        self._winner_ticket = winning_info["order_ticket"]
        self._winner_position_ticket = position["ticket"]
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