from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.settings import UserSettingsOut

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=UserSettingsOut)
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(UserSettings).filter(UserSettings.user_id == current_user.user_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No settings configured for this user yet")
    return row
