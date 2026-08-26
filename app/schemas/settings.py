import uuid

from pydantic import BaseModel


class UserSettingsOut(BaseModel):
    setting_id: uuid.UUID
    instrument: str
    max_daily_loss_pct: float
    news_filters: dict
    demo_or_live: str
    is_paused: bool

    class Config:
        from_attributes = True
