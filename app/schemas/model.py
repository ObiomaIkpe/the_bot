import datetime
import re

from pydantic import BaseModel, field_validator

# Matches the existing fvg/ob/fvg_ob convention: lowercase, starts with
# a letter, alnum + underscore only. Enforced at the app layer (not a
# DB CHECK) since this is the one place new names get created --
# app/models/model.py's model_name column itself stays a plain String.
_MODEL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ModelOut(BaseModel):
    model_name: str
    display_name: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class ModelCreate(BaseModel):
    model_name: str
    display_name: str

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, v: str) -> str:
        if not _MODEL_NAME_PATTERN.match(v):
            raise ValueError(
                "model_name must be lowercase letters, digits, and underscores only, "
                "starting with a letter (e.g. 'fvg_ob')"
            )
        return v

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("display_name must not be blank")
        return stripped


class AdminModelCreateOut(ModelOut):
    # How many existing users just got a new (status="disabled")
    # ModelConfig row for this model -- see
    # app.core.provisioning.provision_model_for_all_users(). Purely
    # informational for the admin UI ("added, and made available to N
    # existing accounts"); new registrations don't count here, they
    # already get it via the normal registration flow.
    backfilled_users: int
