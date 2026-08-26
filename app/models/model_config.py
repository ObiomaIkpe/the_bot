"""
Phase 4: one row per (user, model) -- replaces user_settings'
live_model/shadow_models columns, which assumed exactly one live model
per user. Confirmed design (this phase's chat history): every model
runs independently, detects its own setups, and manages its own trades;
there is no single "the live model" anymore, just a set of models each
individually in one of three states.
"""
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base

# 'disabled': no streaming pipeline exists yet for this model, or it's
#   deliberately turned off. Nothing runs.
# 'shadow': a real streaming pipeline exists and runs continuously,
#   journaling every signal -- same as Phase 3's FVG shadow mode. No
#   real orders.
# 'active': a real streaming pipeline exists and runs, AND its
#   trade_candidate_ready events trigger real orders via the bridge.
#   The only state that touches real money (even demo).
#
# New models always start 'disabled' until their streaming pipeline is
# built (mirrors how FVG itself only started running in Phase 3, long
# after Phase 1 built it). A model moves 'shadow' -> 'active' only via
# an explicit, deliberate status change -- never automatically, and
# never as a side effect of a migration. Same philosophy as the
# bridge's orders_enabled kill switch: default OFF, explicit opt-in.
VALID_MODEL_STATUSES = ("disabled", "shadow", "active")


class ModelConfig(Base):
    __tablename__ = "model_configs"

    config_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    model_name = Column(String, nullable=False)  # 'fvg', 'ob', 'drt', ...
    status = Column(String, nullable=False, server_default="disabled")
    risk_pct = Column(Float, nullable=False)

    # MT5's magic field is a plain integer at the protocol level (not an
    # application choice -- see this phase's chat history for why text
    # isn't possible there). Globally unique across the whole system,
    # not just per user, so /positions filtering is always unambiguous
    # even once a second real account (the friend's) exists.
    magic_number = Column(Integer, nullable=False, unique=True)

    # Nullable = no cap (current confirmed design: "infinite, per
    # model" -- see this phase's chat history). Present as a real column
    # rather than hardcoded so a future model CAN be given a real cap
    # without another migration.
    max_concurrent_positions = Column(Integer, nullable=True)

    # Per-model pause, distinct from UserSettings.is_paused (the
    # account-wide emergency stop -- see that column's own docstring in
    # user_settings.py). This one stops just THIS model without
    # affecting any others the user runs. Both are checked fresh on
    # every trade candidate; either one being true blocks a real order.
    is_paused = Column(Boolean, nullable=False, server_default="false")

    user = relationship("User", back_populates="model_configs")

    __table_args__ = (
        CheckConstraint(
            "status IN ('disabled', 'shadow', 'active')", name="ck_model_configs_status_valid"
        ),
        UniqueConstraint("user_id", "model_name", name="uq_model_configs_user_model"),
    )