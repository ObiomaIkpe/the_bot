import uuid

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class UserSettings(Base):
    """
    Account-wide settings. Per-model settings (status, risk_pct,
    magic_number) moved to ModelConfig as of Phase 4 -- this table used
    to also carry live_model/shadow_models, which assumed exactly one
    live model per user. That assumption no longer holds: see
    ModelConfig's module docstring.
    """

    __tablename__ = "user_settings"

    setting_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    instrument = Column(String, nullable=False)
    max_daily_loss_pct = Column(Float, nullable=False)
    news_filters = Column(JSONB, nullable=False, default=dict)
    demo_or_live = Column(String, nullable=False)  # 'demo' | 'live'

    # Account-wide emergency stop: stops EVERY model this user runs at
    # once, checked fresh on every trade candidate (see
    # order_manager.py's on_trade_candidate_ready()). Deliberately
    # separate from ModelConfig.is_paused (a per-model, finer-grained
    # pause added later) -- this one exists specifically so "stop
    # everything right now" is a single action, not N per-model toggles
    # that could be forgotten under pressure.
    is_paused = Column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="settings")