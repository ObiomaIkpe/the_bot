import datetime
import logging
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.telegram import alert_for_event
from app.models.model_config import ModelConfig
from app.models.user import User
from app.schemas.model_configs import ModelConfigOut, ModelConfigUpdate
from shadow_runner.persistence import write_event

log = logging.getLogger("app.routers.model_configs")

router = APIRouter(prefix="/model-configs", tags=["model-configs"])

_NY_TZ = ZoneInfo("America/New_York")


@router.get("", response_model=list[ModelConfigOut])
def list_model_configs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Also called directly by bridge/scripts/provision_account.ps1 (to
    # read this account's real magic numbers, not just by the admin UI)
    # -- check that script before changing this response shape.
    return (
        db.query(ModelConfig)
        .filter(ModelConfig.user_id == current_user.user_id)
        .order_by(ModelConfig.model_name)
        .all()
    )


@router.patch("/{config_id}", response_model=ModelConfigOut)
def update_model_config(
    config_id: uuid.UUID,
    payload: ModelConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(ModelConfig)
        .filter(ModelConfig.config_id == config_id, ModelConfig.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided to update")

    for field, value in changes.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)

    # 2026-09-04 write-path audit fix: the real change above (e.g.
    # flipping a model to active -- REAL trading starting for this
    # user/model) is already durably committed by this point, in its
    # own separate transaction. A failure in this second commit
    # (journaling model_config_updated) previously had no try/except at
    # all, so it would 500 the request -- misleadingly telling the user
    # their change failed when it had actually already taken effect.
    # Same class of bug as trading.py's close_position/cancel_pending_
    # order, arguably higher-stakes here: a user seeing a false error
    # might not realize their model really is now active (or inactive).
    try:
        write_event(
            db,
            {
                "event_type": "model_config_updated",
                "timestamp": datetime.datetime.now(_NY_TZ).replace(tzinfo=None),
                "changed_fields": changes,
            },
            current_user.user_id,
            row.model_name,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        log.exception(
            "Journaling model_config_updated for config_id=%s failed (the actual change "
            "above already committed regardless) -- attempting to journal the failure itself",
            config_id,
        )
        try:
            write_event(
                db,
                {
                    "event_type": "safety_check_failed",
                    "timestamp": datetime.datetime.now(_NY_TZ).replace(tzinfo=None),
                    "check_name": "model_config_updated_journal_failed",
                    "error": str(e),
                },
                current_user.user_id, row.model_name,
            )
            db.commit()
        except Exception:
            log.exception("Additionally failed to journal the above model-config journaling failure")
            db.rollback()
        else:
            alert_for_event(
                {"event_type": "safety_check_failed", "check_name": "model_config_updated_journal_failed", "error": str(e)},
                current_user.user_id, row.model_name,
            )

    return row
