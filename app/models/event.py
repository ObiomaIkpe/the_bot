import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base

# Extended for Phase 3 (shadow mode). Cross-referenced against every
# event_type actually emitted by the seven streaming components AND the
# original golden-master extraction script (phase1/extract_golden_master.py)
# -- the two lists didn't match, which is exactly the kind of silent gap
# this project's "trace every discrepancy" standard exists to catch.
#
# Original nine (Phase 0/2 era, order_placed/order_filled/order_rejected/
# connection_drop/daily_loss_limit_hit/error are Phase 2-4 broker-adapter
# events, not yet actually emitted by anything as of Phase 3):
#   raid_detected, mss_confirmed, fvg_found, order_placed, order_filled,
#   order_rejected, connection_drop, daily_loss_limit_hit, error
#
# Added for Phase 3 -- every event type the streaming components and
# DaySelectionGate actually produce that wasn't already covered:
VALID_EVENT_TYPES = (
    # -- original --
    "raid_detected",
    "mss_confirmed",
    "fvg_found",
    "order_placed",
    "order_filled",
    "order_rejected",
    "connection_drop",
    "daily_loss_limit_hit",
    "error",
    # -- added, Phase 3: swing detection (daily + intraday) --
    "daily_swing_high_confirmed",
    "daily_swing_low_confirmed",
    "intraday_swing_high_confirmed",
    "intraday_swing_low_confirmed",
    # -- added, Phase 3: FVG rejection (golden master tracks this; the
    #    original list only had the "found" side, not "found but rejected") --
    "fvg_rejected_min_stop",
    # -- added, Phase 3: trade lifecycle (TradeAttempt's simulated fill/
    #    close -- see is_shadow note below for why "order_filled" is
    #    ambiguous once real broker fills exist in Phase 4) --
    "trade_closed",
    # -- added, Phase 3: DaySelectionGate's day-level decisions (mirrors
    #    the golden master's day_skipped_* / day_trend_determined events) --
    "day_skipped_fomc",
    "day_skipped_no_trend",
    "day_skipped_insufficient_bars",
    "day_skipped_no_session_start",
    "day_trend_determined",
    # -- added, Phase 3: DaySelectionGate's FOMC-calendar staleness
    #    self-check (see day_selection_gate.py's module docstring) --
    "fomc_calendar_stale_warning",
    # -- added, Phase 3 step 7: one-time cold-start trend-history
    #    bootstrap marker (see shadow_runner/runner.py's
    #    _bootstrap_trend_history_if_needed()) -- written once, ever,
    #    per (user, model), to prevent re-injecting historical daily
    #    swing data on every future restart --
    "trend_history_bootstrapped",
    # -- added, Phase 4 step 2b: the earliest point a candidate's real
    #    entry+stop are both known -- see day_orchestrator.py's
    #    trade_candidate_ready emit(). The live-order-manager listens for
    #    this to place a real pending order; take_profit is deliberately
    #    absent from this event (unknown until the real fill happens) --
    "trade_candidate_ready",
    # -- added, Phase 4 step 2c: OrderManager's own events (distinct
    #    from DayOrchestrator's -- these describe what the order-manager
    #    actually DID against the real broker, not what the trading
    #    logic detected) --
    "pending_order_placed",
    "pending_order_cancelled",
    "candidate_filled",
    "target_attached",
    "order_placement_failed",
    # -- added, Phase 4 step 3: the real broker-side close of a winning
    #    position -- distinct from the simulation's trade_closed (which
    #    describes what the SIMULATION thinks happened); this is what
    #    ACTUALLY happened, per MT5's own trade history --
    "real_trade_closed",
    # -- added, Phase 4 step 3 (overnight-position handling): the 5pm
    #    NY half-volume risk-reduction event, distinct from a full
    #    close -- see shadow_runner/position_tracker.py --
    "partial_close_executed",
    # -- added, Phase 4 step 4 (safety rails): a candidate that would
    #    have placed a real order, but didn't, because
    #    UserSettings.is_paused was true at the moment it was checked --
    "order_skipped_paused",
    # -- added, Phase 4 step 4 Part 2 (visibility only, confirmed design:
    #    does NOT block new trades or force-close anything -- purely
    #    journals that today's realized loss crossed
    #    UserSettings.max_daily_loss_pct, for awareness) --
    "daily_loss_threshold_crossed",
)

# Fixes a real, stale bug: write_event() used to hardcode is_shadow=True
# on every Event row, unconditionally -- correct back when nothing but
# DayOrchestrator/DaySelectionGate ever emitted events (Phase 3), wrong
# now that OrderManager emits real-action events too (Phase 4).
#
# The distinguishing fact that makes this decidable from event_type
# ALONE, with no per-call-site changes needed anywhere: OrderManager
# only ever calls its own event_sink when is_active() is True (its very
# first check in on_trade_candidate_ready() returns immediately
# otherwise) -- so every event type it emits is, by construction,
# ALWAYS describing a real action, never a merely-simulated one. Every
# other event type (DayOrchestrator's raid/MSS/FVG/trade detection,
# DaySelectionGate's day-level decisions) always describes internal
# detection/simulation logic, regardless of whether the model is active
# -- that never changes into a "real action" no matter what.
#
# See persistence.py's write_event() for where this is actually used.
# Unrecognized event types default to is_shadow=True (the safe
# direction: never mistakenly claim something was a real action when
# it isn't a known real-action type).
REAL_ACTION_EVENT_TYPES = frozenset(
    {
        "pending_order_placed",
        "pending_order_cancelled",
        "candidate_filled",
        "target_attached",
        "order_placement_failed",
        "order_skipped_paused",
        "real_trade_closed",
        "partial_close_executed",
        "daily_loss_threshold_crossed",
    }
)


class Event(Base):
    """
    Every one of these event types is currently detected-and-discarded
    inside the backtest loop; live, they become durable, queryable records
    instead of silently vanishing.
    """

    __tablename__ = "events"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    model = Column(String, nullable=False)  # 'fvg' / 'ob' / 'fvg_ob'

    event_type = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    details = Column(JSONB, nullable=False, default=dict)

    # Added for Phase 3. Without this, an "order_filled" or "trade_closed"
    # row is ambiguous once Phase 4 exists -- there'd be no way to tell a
    # shadow-mode simulated fill apart from a real broker fill except by
    # cross-referencing the trades table's is_shadow flag (which not every
    # event has a matching trade row for, e.g. day_skipped_* events).
    # Mirrors Trade.is_shadow exactly. Every event this project emits
    # before Phase 4 ships real order code is_shadow=True; nothing sets
    # it False yet.
    is_shadow = Column(Boolean, nullable=False, server_default="true")

    user = relationship("User", back_populates="events")

    __table_args__ = (
        CheckConstraint("model IN ('fvg', 'ob', 'fvg_ob')", name="ck_events_model_valid"),
        # Not a hard DB-level enum on event_type -- new event types are
        # likely as later phases add detail (e.g. partial fills in Phase
        # 4), and a CHECK constraint would need a migration every time.
        # VALID_EVENT_TYPES above is the source of truth at the app layer.
    )