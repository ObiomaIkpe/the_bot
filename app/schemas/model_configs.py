import uuid

from pydantic import BaseModel, field_validator

from app.models.model_config import VALID_MODEL_STATUSES


class ModelConfigOut(BaseModel):
    config_id: uuid.UUID
    model_name: str
    status: str
    risk_pct: float
    magic_number: int
    max_concurrent_positions: int | None
    is_paused: bool

    class Config:
        from_attributes = True


class ModelConfigUpdate(BaseModel):
    """Both fields optional -- a PATCH may change either or both."""
    status: str | None = None
    is_paused: bool | None = None

    @field_validator("status")
    @classmethod
    def _status_must_be_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_MODEL_STATUSES:
            raise ValueError(f"status must be one of {VALID_MODEL_STATUSES}")
        return v
