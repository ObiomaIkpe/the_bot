import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.trade_story import build_trade_chain
from app.models.trade import Trade
from app.models.user import User
from app.schemas.events import EventOut
from app.schemas.trades import TradeEventChainOut, TradeOut

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


@router.get("/{trade_id}/event-chain", response_model=TradeEventChainOut)
def get_trade_event_chain(
    trade_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The trader-facing "why was this trade placed" story -- see
    app.core.trade_story.build_trade_chain()'s module docstring for how
    the chain is walked. Ownership enforced (unlike admin's equivalent
    endpoint, which deliberately has none): 404 if the trade doesn't
    exist OR belongs to someone else, so this never leaks that a trade
    id exists for another user."""
    trade = (
        db.query(Trade)
        .filter(Trade.trade_id == trade_id, Trade.user_id == current_user.user_id)
        .first()
    )
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")

    result = build_trade_chain(db, trade)
    return TradeEventChainOut(
        chain=[EventOut.from_model(e) for e in result.chain],
        fully_resolved=result.fully_resolved,
    )
