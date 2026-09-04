import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Trade(Base):
    __tablename__ = "trades"

    trade_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable since migration 0021 (multi-user fan-out, piece 2) -- NULL
    # means the model's own ownerless "shadow" row (is_shadow=True,
    # real_outcome always None), written once per model per day
    # regardless of how many real subscribers exist. Every per-subscriber
    # REAL-outcome row still always carries a real user_id (
    # get_active_subscribers() only ever returns real users) -- this is
    # NOT a general relaxation of trade ownership, just the one shared row.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True, index=True)

    # FK to models.model_name (migration 0018) -- previously a hardcoded
    # CHECK constraint (ck_trades_model_valid, 'fvg'/'ob'/'fvg_ob' only);
    # see app/models/model.py's module docstring for why.
    model = Column(String, ForeignKey("models.model_name"), nullable=False)
    is_shadow = Column(Boolean, nullable=False)  # True = journaled only, no real order

    direction = Column(String, nullable=False)  # 'long' / 'short'
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)  # null until the trade closes
    outcome = Column(String, nullable=True)  # 'win' / 'loss' / 'scratch', null while open
    realized_r = Column(Float, nullable=True)

    # This project has already put real care into NY-time conversion for
    # the backtest (HistData's fixed-EST convention -> true DST-aware
    # America/New_York wall-clock time) -- the live feed needs the same
    # rigor, hence storing both UTC and NY entry time rather than deriving
    # NY time ad hoc later.
    entry_time_utc = Column(DateTime(timezone=True), nullable=False)
    entry_time_ny = Column(DateTime(timezone=True), nullable=False)
    exit_time_utc = Column(DateTime(timezone=True), nullable=True)

    risk_pct_used = Column(Float, nullable=False)
    equity_before = Column(Float, nullable=False)
    equity_after = Column(Float, nullable=True)  # null until closed

    # In practice only ever holds {trend, risk_pips} (see
    # shadow_runner/runner.py's _write_trade()) -- this was originally
    # meant to also carry which swing was raided, which candle
    # confirmed the MSS, which candles formed the FVG, but that never
    # got built here. That richer "why" now lives in
    # app.core.trade_story.build_trade_chain() instead, computed at
    # read time from the `events` table rather than duplicated into
    # this column -- see GET /trades/{trade_id}/event-chain. Kept as
    # JSONB so this can still grow without a migration if ever needed.
    setup_context = Column(JSONB, nullable=False, default=dict)

    # Phase 4 step 3 (part 2): the REAL broker-side outcome, for trades
    # where a real order was actually placed (is_shadow=False) --
    # entry_price/exit_price/outcome/realized_r above remain what they
    # always were: the SIMULATION's view (DayOrchestrator/TradeAttempt's
    # computed result), unchanged by this addition. These columns hold
    # what actually happened, side by side, so the two can be compared
    # directly on one row without a join -- this is exactly the
    # reconciliation Phase 4 step 3 exists to build. All nullable: a
    # shadow-mode trade (still the overwhelming majority of rows) never
    # populates any of these; even for an is_shadow=False trade, they
    # only populate once OrderManager's real fill/close detection has
    # actually happened (see shadow_runner/order_manager.py's
    # get_real_outcome()).
    # BigInteger since migration 0022 -- was a plain 32-bit Integer
    # (max 2,147,483,647), which every real MT5 ticket actually observed
    # 2026-09-04 already exceeded, silently failing every real trade
    # write that reached this column (discarding the whole row, not just
    # this field, since it's one INSERT) -- confirmed live: zero rows
    # ever had this populated, despite weeks of real trading.
    real_position_ticket = Column(BigInteger, nullable=True)
    real_fill_price = Column(Float, nullable=True)
    real_fill_time_utc = Column(DateTime(timezone=True), nullable=True)
    real_fill_time_ny = Column(DateTime(timezone=True), nullable=True)
    real_close_price = Column(Float, nullable=True)
    real_close_time_utc = Column(DateTime(timezone=True), nullable=True)
    real_close_time_ny = Column(DateTime(timezone=True), nullable=True)
    real_profit = Column(Float, nullable=True)
    real_close_reason = Column(String, nullable=True)  # 'stop_loss' | 'take_profit' | 'manual' | 'expert' | 'unknown'

    # Phase 4 step 3 (overnight-position handling): a real trade's own
    # lifecycle, tracked independently of `outcome` (the SIMULATION's
    # same-day result, fixed at day-finalize time and never revisited).
    # null for shadow trades. See shadow_runner/position_tracker.py's
    # module docstring for the full design -- a real position no longer
    # necessarily resolves same-day; if still open at 5pm NY, half its
    # volume closes (partial_close_* below) and the rest keeps running
    # to natural resolution (real_close_* above, from migration 0004),
    # however many days that takes.
    real_status = Column(String, nullable=True)  # 'open' | 'partial_closed' | 'closed'
    partial_close_price = Column(Float, nullable=True)
    partial_close_time_utc = Column(DateTime(timezone=True), nullable=True)
    partial_close_time_ny = Column(DateTime(timezone=True), nullable=True)
    partial_close_volume = Column(Float, nullable=True)
    partial_close_profit = Column(Float, nullable=True)

    user = relationship("User", back_populates="trades")

    __table_args__ = (
        CheckConstraint("direction IN ('long', 'short')", name="ck_trades_direction_valid"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('win', 'loss', 'scratch')",
            name="ck_trades_outcome_valid",
        ),
        CheckConstraint(
            "real_status IS NULL OR real_status IN ('open', 'partial_closed', 'closed')",
            name="ck_trades_real_status_valid",
        ),
    )