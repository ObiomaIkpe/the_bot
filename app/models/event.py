import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base

VALID_EVENT_TYPES = (
    "raid_detected",
    "mss_confirmed",
    "fvg_found",
    "order_placed",
    "order_filled",
    "order_rejected",
    "connection_drop",
    "daily_loss_limit_hit",
    "error",
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

    user = relationship("User", back_populates="events")

    __table_args__ = (
        CheckConstraint("model IN ('fvg', 'ob', 'fvg_ob')", name="ck_events_model_valid"),
        # Not a hard DB-level enum on event_type -- new event types are
        # likely as later phases add detail (e.g. partial fills in Phase
        # 4), and a CHECK constraint would need a migration every time.
        # VALID_EVENT_TYPES above is the source of truth at the app layer.
    )
