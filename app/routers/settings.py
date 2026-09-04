import datetime
import logging
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.telegram import alert_for_event
from app.models.model_config import ModelConfig
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.settings import UserSettingsOut, UserSettingsUpdate
from shadow_runner.persistence import write_event

log = logging.getLogger("app.routers.settings")

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
    #
    # 2026-09-04 write-path audit fix: the real change above (line 47's
    # commit) -- which can be is_paused, the account-wide emergency
    # stop -- is already durably committed by this point, in its own
    # transaction. Without a try/except here, a failure journaling it
    # would 500 the request, misleadingly telling the user their
    # pause/unpause failed when it had actually already taken effect --
    # arguably the highest-stakes instance of this bug class found in
    # this whole audit pass, given what is_paused actually controls.
    user_model_names = []
    try:
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
    except Exception as e:
        db.rollback()
        log.exception(
            "Journaling account_settings_updated for user_id=%s failed (the actual change "
            "above already committed regardless) -- attempting to journal the failure itself",
            current_user.user_id,
        )
        # No specific model to attach this failure event to (the whole
        # point of the block that just failed was fanning out across
        # every one of the user's models) -- best-effort against the
        # first one, if any, same "something beats nothing" reasoning
        # used elsewhere in this pass. model may not be set if the
        # query above itself is what failed.
        fallback_model = user_model_names[0] if user_model_names else None
        try:
            if fallback_model is not None:
                write_event(
                    db,
                    {
                        "event_type": "safety_check_failed",
                        "timestamp": datetime.datetime.now(_NY_TZ).replace(tzinfo=None),
                        "check_name": "account_settings_updated_journal_failed",
                        "error": str(e),
                    },
                    current_user.user_id, fallback_model,
                )
                db.commit()
        except Exception:
            log.exception("Additionally failed to journal the above account-settings journaling failure")
            db.rollback()
        else:
            if fallback_model is not None:
                alert_for_event(
                    {"event_type": "safety_check_failed", "check_name": "account_settings_updated_journal_failed", "error": str(e)},
                    current_user.user_id, fallback_model,
                )

    return row
