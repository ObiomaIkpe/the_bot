import datetime
import uuid

from pydantic import BaseModel

from app.core.event_narration import narrate_event


class EventOut(BaseModel):
    event_id: uuid.UUID
    model: str
    event_type: str
    timestamp: datetime.datetime
    details: dict
    is_shadow: bool
    trade_id: uuid.UUID | None = None
    # Plain-English rendering of (event_type, details) -- see
    # app.core.event_narration. Computed here, not stored, so every
    # existing consumer of EventOut gets it for free without a backfill.
    narrative: str = ""

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, event) -> "EventOut":
        return cls(
            event_id=event.event_id,
            model=event.model,
            event_type=event.event_type,
            timestamp=event.timestamp,
            details=event.details,
            is_shadow=event.is_shadow,
            trade_id=event.trade_id,
            narrative=narrate_event(event.event_type, event.details),
        )
