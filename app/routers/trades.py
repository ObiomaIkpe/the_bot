import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.trade import Trade
from app.models.user import User
from app.schemas.trades import TradeOut

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("", response_model=list[TradeOut])
def list_trades(
    model: str | None = None,
    is_shadow: bool | None = None,
    outcome: str | None = None,
    days_back: int | None = None,
    limit: int = Query(default=200, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Trade).filter(Trade.user_id == current_user.user_id)
    if model:
        q = q.filter(Trade.model == model)
    if is_shadow is not None:
        q = q.filter(Trade.is_shadow == is_shadow)
    if outcome:
        q = q.filter(Trade.outcome == outcome)
    if days_back:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days_back)
        q = q.filter(Trade.entry_time_utc >= cutoff)
    return q.order_by(Trade.entry_time_ny.desc()).limit(limit).all()
