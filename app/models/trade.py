import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Trade(Base):
    __tablename__ = "trades"

    trade_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    model = Column(String, nullable=False)  # 'fvg' / 'ob' / 'fvg_ob'
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

    # Which swing was raided, which candle confirmed the MSS, which
    # candles formed the FVG, OB-confirmed or not -- kept as JSONB so this
    # can grow without a schema migration every time a new field is added.
    setup_context = Column(JSONB, nullable=False, default=dict)

    user = relationship("User", back_populates="trades")

    __table_args__ = (
        CheckConstraint("model IN ('fvg', 'ob', 'fvg_ob')", name="ck_trades_model_valid"),
        CheckConstraint("direction IN ('long', 'short')", name="ck_trades_direction_valid"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('win', 'loss', 'scratch')",
            name="ck_trades_outcome_valid",
        ),
    )
