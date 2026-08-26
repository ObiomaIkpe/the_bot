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


class UserSettingsUpdate(BaseModel):
    """All fields optional -- a PATCH may change any subset. is_paused
    here is the account-wide emergency stop (see UserSettings.is_paused's
    own docstring) -- distinct from a single model's ModelConfig.is_paused."""
    max_daily_loss_pct: float | None = None
    news_filters: dict | None = None
    is_paused: bool | None = None
