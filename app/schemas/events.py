import datetime
import uuid

from pydantic import BaseModel


class EventOut(BaseModel):
    event_id: uuid.UUID
    model: str
    event_type: str
    timestamp: datetime.datetime
    details: dict
    is_shadow: bool

    class Config:
        from_attributes = True
