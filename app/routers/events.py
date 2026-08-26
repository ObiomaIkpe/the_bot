import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.event import Event
from app.models.user import User
from app.schemas.events import EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(
    model: str | None = None,
    since: datetime.datetime | None = None,
    limit: int = Query(default=200, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Event).filter(Event.user_id == current_user.user_id)
    if model:
        q = q.filter(Event.model == model)
    if since:
        q = q.filter(Event.timestamp >= since)
    return q.order_by(Event.timestamp.desc()).limit(limit).all()
