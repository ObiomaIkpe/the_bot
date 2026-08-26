import datetime
import uuid

from pydantic import BaseModel


class TradeOut(BaseModel):
    trade_id: uuid.UUID
    model: str
    is_shadow: bool

    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float | None
    outcome: str | None
    realized_r: float | None

    entry_time_utc: datetime.datetime
    entry_time_ny: datetime.datetime
    exit_time_utc: datetime.datetime | None

    real_status: str | None
    real_fill_price: float | None
    real_close_price: float | None
    real_close_reason: str | None
    real_profit: float | None

    class Config:
        from_attributes = True
