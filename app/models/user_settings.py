import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base

VALID_MODELS = ("fvg", "ob", "fvg_ob")


class UserSettings(Base):
    __tablename__ = "user_settings"

    setting_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, unique=True, index=True
    )

    instrument = Column(String, nullable=False, default="EURUSD")  # flexible, not hardcoded

    # Per §1b of LIVE_BOT_BUILD_PLAN.md: exactly one model places real
    # orders; the rest run in shadow mode. Enforced at the DB level below
    # (live_model can't also appear in shadow_models) and should also be
    # validated at the API layer before it ever reaches here.
    live_model = Column(String, nullable=False)
    shadow_models = Column(ARRAY(String), nullable=False, default=list)

    risk_pct = Column(Float, nullable=False)  # per-trade; per-tier if live_model = fvg_ob
    max_daily_loss_pct = Column(Float, nullable=False)
    max_concurrent_positions = Column(Integer, nullable=False, default=1)

    # e.g. {"FOMC": true, "NFP": false, "ECB": false, "CPI": false}
    # true = excluded. FOMC defaults true per the locked backtest.
    news_filters = Column(
        JSONB,
        nullable=False,
        default=lambda: {"FOMC": True, "NFP": False, "ECB": False, "CPI": False},
    )

    demo_or_live = Column(String, nullable=False, default="demo")  # the most consequential flag
    is_paused = Column(Boolean, nullable=False, default=False)  # manual kill switch

    user = relationship("User", back_populates="settings")

    __table_args__ = (
        CheckConstraint(
            "live_model IN ('fvg', 'ob', 'fvg_ob')", name="ck_user_settings_live_model_valid"
        ),
        CheckConstraint(
            "demo_or_live IN ('demo', 'live')", name="ck_user_settings_demo_or_live_valid"
        ),
        CheckConstraint(
            "NOT (live_model = ANY(shadow_models))",
            name="ck_user_settings_live_not_in_shadow",
        ),
    )
