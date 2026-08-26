import uuid

from pydantic import BaseModel


class ModelConfigOut(BaseModel):
    config_id: uuid.UUID
    model_name: str
    status: str
    risk_pct: float
    magic_number: int
    max_concurrent_positions: int | None

    class Config:
        from_attributes = True
