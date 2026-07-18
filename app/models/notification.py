import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Notification(Base):
    """
    NOTE: LIVE_BOT_BUILD_PLAN.md's notifications table definition was
    truncated in the version I read (rows after 'destination' were cut
    off). I've filled in the remaining columns with what the surrounding
    text implies is needed -- event_id to link back to what triggered it,
    a status to track delivery, and created_at/sent_at for the alerting
    described in section 3 (fills, rejections, connection drops, daily-
    loss-limit breaches, errors all page the user's email). Worth
    double-checking against your original if you have the untruncated
    version.
    """

    __tablename__ = "notifications"

    notification_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    channel_type = Column(String, nullable=False, default="email")  # 'email' now, 'slack'/'discord' later
    destination = Column(String, nullable=False)  # email address now, webhook URL later

    event_id = Column(UUID(as_uuid=True), ForeignKey("events.event_id"), nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending / sent / failed
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="notifications")
