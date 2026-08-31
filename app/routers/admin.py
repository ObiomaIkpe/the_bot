"""
Admin-only endpoints -- replaces the separate, unscoped Streamlit
admin_dashboard/ tool with real, gated API routes inside the same
FastAPI app the rest of the frontend already talks to. Every route here
is Depends(get_current_admin): same JWT as every other endpoint, plus
an authorization check (see app/core/deps.py). Deliberately its own
router/prefix, not folded into events.py/trades.py/model_configs.py --
those stay single-user-scoped for every ordinary caller; this file is
the one place in the whole API that reads across ALL users on purpose.
"""
import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.provisioning import provision_model_for_all_users
from app.models.audit_log import AuditLog
from app.models.event import Event
from app.models.model import Model
from app.models.model_config import ModelConfig
from app.models.trade import Trade
from app.models.user import User
from app.schemas.admin import (
    AdminAuditLogOut,
    AdminEventChainOut,
    AdminEventOut,
    AdminModelConfigOut,
    AdminTradeOut,
)
from app.schemas.model import AdminModelCreateOut, ModelCreate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/events", response_model=list[AdminEventOut])
def list_all_events(
    model: str | None = None,
    since: datetime.datetime | None = None,
    limit: int = Query(default=200, le=1000),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Same shape as GET /events, minus the user_id scoping -- see that
    router for the single-user equivalent."""
    q = db.query(Event, User).join(User, Event.user_id == User.user_id)
    if model:
        q = q.filter(Event.model == model)
    if since:
        q = q.filter(Event.timestamp >= since)
    rows = q.order_by(Event.timestamp.desc()).limit(limit).all()
    return [AdminEventOut.from_model(event, user) for event, user in rows]


@router.get("/safety-checks", response_model=list[AdminEventOut])
def list_all_safety_check_failures(
    since: datetime.datetime | None = None,
    limit: int = Query(default=200, le=1000),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """safety_check_failed events specifically -- always
    REAL_ACTION_EVENT_TYPES (see app/models/event.py), i.e. always
    something that genuinely went wrong in the live/real-money path."""
    q = (
        db.query(Event, User)
        .join(User, Event.user_id == User.user_id)
        .filter(Event.event_type == "safety_check_failed")
    )
    if since:
        q = q.filter(Event.timestamp >= since)
    rows = q.order_by(Event.timestamp.desc()).limit(limit).all()
    return [AdminEventOut.from_model(event, user) for event, user in rows]


@router.get("/trades", response_model=list[AdminTradeOut])
def list_all_trades(
    model: str | None = None,
    is_shadow: bool | None = None,
    outcome: str | None = None,
    days_back: int | None = None,
    limit: int = Query(default=200, le=1000),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Same shape as GET /trades, minus the user_id scoping."""
    q = db.query(Trade, User).join(User, Trade.user_id == User.user_id)
    if model:
        q = q.filter(Trade.model == model)
    if is_shadow is not None:
        q = q.filter(Trade.is_shadow == is_shadow)
    if outcome:
        q = q.filter(Trade.outcome == outcome)
    if days_back:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days_back)
        q = q.filter(Trade.entry_time_utc >= cutoff)
    rows = q.order_by(Trade.entry_time_ny.desc()).limit(limit).all()
    return [AdminTradeOut.from_model(trade, user) for trade, user in rows]


@router.get("/trades/{trade_id}/event-chain", response_model=AdminEventChainOut)
def get_trade_event_chain(
    trade_id: uuid.UUID,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Ports admin_dashboard/queries.py's get_event_chain_for_trade()
    server-side. `day_events` (the full day's event feed, for context) is
    always found by (user, model) + NY calendar date -- that part hasn't
    changed. Which specific events are the trade's fill/close, though, now
    prefers the real events.trade_id FK (logging/audit review part 3):
    shadow_runner/runner.py's _write_trade() sets it directly, so this is
    an exact match, not a re-derived one.

    Falls back to the old heuristic (direction/price matching within the
    day) only for historical events written before that FK existed and
    never backfilled -- see app/scripts/backfill_event_trade_ids.py. Once
    that's been run everywhere, this fallback is dead code but harmless to
    leave in place.

    No ownership check beyond existing -- an admin can look up any
    trade's chain regardless of which user it belongs to.
    """
    trade = db.query(Trade).filter(Trade.trade_id == trade_id).first()
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")

    user = db.query(User).filter(User.user_id == trade.user_id).first()

    day = trade.entry_time_ny.date()
    day_start = datetime.datetime.combine(day, datetime.time.min)
    day_end = day_start + datetime.timedelta(days=1)
    day_events = (
        db.query(Event)
        .filter(
            Event.user_id == trade.user_id,
            Event.model == trade.model,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
        .order_by(Event.timestamp.asc())
        .all()
    )

    matched_fill = next((e for e in day_events if e.trade_id == trade.trade_id and e.event_type == "order_filled"), None)
    matched_close = next((e for e in day_events if e.trade_id == trade.trade_id and e.event_type == "trade_closed"), None)

    if matched_fill is None:
        matched_fill = next(
            (
                e for e in day_events
                if e.event_type == "order_filled"
                and e.details.get("direction") == trade.direction
                and e.details.get("entry") is not None
                and abs(e.details["entry"] - trade.entry_price) < 1e-9
            ),
            None,
        )
    if matched_close is None:
        matched_close = next(
            (
                e for e in reversed(day_events)
                if e.event_type == "trade_closed"
                and e.details.get("outcome") == trade.outcome
                and e.details.get("exit_price") is not None
                and trade.exit_price is not None
                and abs(e.details["exit_price"] - trade.exit_price) < 1e-9
            ),
            None,
        )

    return AdminEventChainOut(
        day_events=[AdminEventOut.from_model(e, user) for e in day_events],
        matched_fill_event_id=matched_fill.event_id if matched_fill else None,
        matched_close_event_id=matched_close.event_id if matched_close else None,
    )


@router.get("/audit-log", response_model=list[AdminAuditLogOut])
def list_audit_log(
    actor_type: str | None = None,
    event_type: str | None = None,
    since: datetime.datetime | None = None,
    limit: int = Query(default=200, le=1000),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Security/identity events (auth, broker credential lifecycle,
    provisioning/decommission job transitions, the plaintext-credential
    fetch) -- NOT the trading pipeline's own events, see /admin/events
    for those. Not scoped to one user by design -- see
    app/models/audit_log.py."""
    q = db.query(AuditLog)
    if actor_type:
        q = q.filter(AuditLog.actor_type == actor_type)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    if since:
        q = q.filter(AuditLog.timestamp >= since)
    rows = q.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return [AdminAuditLogOut.from_model(r) for r in rows]


@router.get("/model-configs", response_model=list[AdminModelConfigOut])
def list_all_model_configs(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Same shape as GET /model-configs, minus the user_id scoping.
    Read-only -- there is no admin PATCH here, same as the Streamlit
    tool this replaces (editing another user's config stays out of
    scope; see the plan this was built from)."""
    rows = (
        db.query(ModelConfig, User)
        .join(User, ModelConfig.user_id == User.user_id)
        .order_by(User.email, ModelConfig.model_name)
        .all()
    )
    return [AdminModelConfigOut.from_model(config, user) for config, user in rows]


@router.post("/models", response_model=AdminModelCreateOut, status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelCreate,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Registers a new model (the `models` table, app/models/model.py)
    and immediately backfills a ModelConfig row for every existing user
    -- see provision_model_for_all_users()'s docstring for why that's
    done here rather than requiring a separate script run. This is the
    ONE place a model gets created; every other model-aware dropdown
    across the app (GET /models, admin and trader-facing alike) just
    reads what's here."""
    model = Model(model_name=payload.model_name, display_name=payload.display_name)
    db.add(model)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Model '{payload.model_name}' already exists",
        )
    db.refresh(model)

    backfilled = provision_model_for_all_users(db, payload.model_name)

    return AdminModelCreateOut(
        model_name=model.model_name,
        display_name=model.display_name,
        created_at=model.created_at,
        backfilled_users=backfilled,
    )
