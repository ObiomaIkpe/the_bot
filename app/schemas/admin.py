import datetime
import uuid

from pydantic import BaseModel

from app.core.event_narration import narrate_event
from app.models.audit_log import AuditLog
from app.models.event import Event
from app.models.model_config import ModelConfig
from app.models.trade import Trade
from app.models.user import User

# Every schema here adds exactly one field (user_email) to its
# single-user equivalent in app/schemas/events.py / trades.py /
# model_configs.py -- admin_dashboard/'s own Streamlit tables never
# showed which user a row belonged to at all (it had no per-user
# concept), so this is a genuine improvement, not just a straight port.
# Uses the from_model() classmethod convention (see
# BrokerCredentialOut) since these need a joined User row, not just a
# straight column mirror.


class AdminEventOut(BaseModel):
    event_id: uuid.UUID
    # Multi-user fan-out, piece 1.5: None for a shared, ownerless
    # narrative row (event.user_id IS NULL -- see
    # app.models.event.NARRATIVE_EVENT_TYPES) -- there's no User row to
    # join for those. Real-action events (a specific account's fill,
    # close, safety-check-failed, etc.) always still carry a real email.
    user_email: str | None
    model: str
    event_type: str
    timestamp: datetime.datetime
    details: dict
    is_shadow: bool
    # See app.core.event_narration / app.schemas.events.EventOut --
    # same plain-English rendering, computed here too so the admin
    # event feed isn't a step behind the trader-facing one.
    narrative: str = ""

    @classmethod
    def from_model(cls, event: Event, user: User | None) -> "AdminEventOut":
        return cls(
            event_id=event.event_id,
            user_email=user.email if user is not None else None,
            model=event.model,
            event_type=event.event_type,
            timestamp=event.timestamp,
            details=event.details,
            is_shadow=event.is_shadow,
            narrative=narrate_event(event.event_type, event.details),
        )


class AdminTradeOut(BaseModel):
    trade_id: uuid.UUID
    # Multi-user fan-out, piece 2: None for the model's ownerless shadow
    # row (trade.user_id IS NULL -- see migration 0021) -- there's no
    # User row to join for that one. Every per-subscriber real-outcome
    # row always still carries a real email.
    user_email: str | None
    model: str
    is_shadow: bool

    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float | None
    outcome: str | None
    realized_r: float | None
    # % of equity actually risked on THIS trade -- see TradeOut's own
    # comment on the same field for why this differs from the model's
    # current configured risk_pct.
    risk_pct_used: float

    entry_time_utc: datetime.datetime
    entry_time_ny: datetime.datetime
    exit_time_utc: datetime.datetime | None

    real_status: str | None
    real_fill_price: float | None
    real_close_price: float | None
    real_close_reason: str | None
    real_profit: float | None

    @classmethod
    def from_model(cls, trade: Trade, user: User | None) -> "AdminTradeOut":
        return cls(
            trade_id=trade.trade_id,
            user_email=user.email if user is not None else None,
            model=trade.model,
            is_shadow=trade.is_shadow,
            direction=trade.direction,
            entry_price=trade.entry_price,
            stop_price=trade.stop_price,
            target_price=trade.target_price,
            exit_price=trade.exit_price,
            outcome=trade.outcome,
            realized_r=trade.realized_r,
            risk_pct_used=trade.risk_pct_used,
            entry_time_utc=trade.entry_time_utc,
            entry_time_ny=trade.entry_time_ny,
            exit_time_utc=trade.exit_time_utc,
            real_status=trade.real_status,
            real_fill_price=trade.real_fill_price,
            real_close_price=trade.real_close_price,
            real_close_reason=trade.real_close_reason,
            real_profit=trade.real_profit,
        )


class AdminModelConfigOut(BaseModel):
    config_id: uuid.UUID
    user_email: str
    model_name: str
    status: str
    risk_pct: float
    magic_number: int
    max_concurrent_positions: int | None
    is_paused: bool

    @classmethod
    def from_model(cls, config: ModelConfig, user: User) -> "AdminModelConfigOut":
        return cls(
            config_id=config.config_id,
            user_email=user.email,
            model_name=config.model_name,
            status=config.status,
            risk_pct=config.risk_pct,
            magic_number=config.magic_number,
            max_concurrent_positions=config.max_concurrent_positions,
            is_paused=config.is_paused,
        )


class AdminAuditLogOut(BaseModel):
    """No user join needed -- unlike Event/Trade/ModelConfig, AuditLog
    already carries a denormalized, human-readable actor_label (see
    app/models/audit_log.py), and actor_id isn't even always a user
    (machine/credential actors have no User row at all)."""
    audit_id: uuid.UUID
    timestamp: datetime.datetime
    actor_type: str
    actor_id: uuid.UUID | None
    actor_label: str | None
    event_type: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    details: dict
    ip_address: str | None

    @classmethod
    def from_model(cls, row: AuditLog) -> "AdminAuditLogOut":
        return cls(
            audit_id=row.audit_id,
            timestamp=row.timestamp,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            actor_label=row.actor_label,
            event_type=row.event_type,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            details=row.details,
            ip_address=row.ip_address,
        )


class AdminEventChainOut(BaseModel):
    """The full day's events for a trade's (user, model, NY calendar
    date), plus which two (if any) are this specific trade's fill and
    close -- ports admin_dashboard/queries.py's get_event_chain_for_trade()
    matching logic server-side. matched_fill_event_id/matched_close_event_id
    are null when no event matched (mirrors the Streamlit tool's own
    "no direct foreign key" caveat -- this is a best-effort match, not
    a guarantee)."""
    day_events: list[AdminEventOut]
    matched_fill_event_id: uuid.UUID | None
    matched_close_event_id: uuid.UUID | None
