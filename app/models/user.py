import uuid

from sqlalchemy import Boolean, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Gates app/core/deps.py's get_current_admin and every app/routers/
    # admin.py endpoint. Defaults to True for every user (0019) -- an
    # explicit policy change, not the column's original design (0016
    # shipped server_default=false, "no self-service way to become an
    # admin"). Still no HTTP endpoint to change your own value either
    # way; revoke a specific account via app/scripts/promote_to_admin.py
    # --revoke if that's ever needed.
    is_admin = Column(Boolean, nullable=False, server_default="true")

    broker_credentials = relationship(
        "BrokerCredential", back_populates="user", cascade="all, delete-orphan"
    )
    settings = relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    # Phase 4: one row per (user, model) -- replaces the old single
    # live_model/shadow_models columns on UserSettings. See
    # app/models/model_config.py's module docstring.
    model_configs = relationship(
        "ModelConfig", back_populates="user", cascade="all, delete-orphan"
    )
    trades = relationship("Trade", back_populates="user", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )