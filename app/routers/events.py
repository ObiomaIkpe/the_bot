import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
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
    # Multi-user fan-out, piece 1.5: a shared, ownerless narrative row
    # (user_id IS NULL -- see app.models.event.NARRATIVE_EVENT_TYPES)
    # isn't anyone's private data, so it's included alongside this
    # user's own personal/real-action events -- no ownership check
    # needed for it, unlike a real ownership boundary would require.
    q = db.query(Event).filter(or_(Event.user_id == current_user.user_id, Event.user_id.is_(None)))
    if model:
        q = q.filter(Event.model == model)
    if since:
        q = q.filter(Event.timestamp >= since)
    rows = q.order_by(Event.timestamp.desc()).limit(limit).all()
    return [EventOut.from_model(e) for e in rows]
