import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.model_config import ModelConfig
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.settings import UserSettingsOut, UserSettingsUpdate
from shadow_runner.persistence import write_event

router = APIRouter(prefix="/settings", tags=["settings"])

_NY_TZ = ZoneInfo("America/New_York")


@router.get("", response_model=UserSettingsOut)
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(UserSettings).filter(UserSettings.user_id == current_user.user_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No settings configured for this user yet")
    return row


@router.patch("", response_model=UserSettingsOut)
def update_settings(
    payload: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(UserSettings).filter(UserSettings.user_id == current_user.user_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No settings configured for this user yet")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided to update")

    for field, value in changes.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)

    # This action is account-wide, not about any one model -- but
    # Event.model is required and constrained to a real model name (see
    # ADMIN_FRONTEND_PLAN.md's "settings audit event" decision). Fan out
    # one event per model this user actually has configured, since an
    # account-wide change (especially is_paused) genuinely affects every
    # one of them. No-op if the user has zero model_configs yet.
    user_model_names = [
        mc.model_name
        for mc in db.query(ModelConfig).filter(ModelConfig.user_id == current_user.user_id).all()
    ]
    now = datetime.datetime.now(_NY_TZ).replace(tzinfo=None)
    for model_name in user_model_names:
        write_event(
            db,
            {"event_type": "account_settings_updated", "timestamp": now, "changed_fields": changes},
            current_user.user_id,
            model_name,
        )
    db.commit()

    return row
