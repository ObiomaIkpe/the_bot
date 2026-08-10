"""
Every query here is read-only (see db.py for the DB-level enforcement
of that). Nothing in this file ever calls session.commit() or
session.add() -- there is genuinely nothing to write.
"""
from datetime import datetime, timedelta

from db import Event, ModelConfig, Trade

# Mirrors app/models/event.py's own REAL_ACTION_EVENT_TYPES -- imported
# rather than redefined so this can't silently drift from the real list.
from app.models.event import REAL_ACTION_EVENT_TYPES


def get_recent_events(session, model=None, event_types=None, since=None, limit=500):
    """Most recent events first. `model` is a single model name or
    None (all models). `event_types` is a list or None (all types).
    `since` is a datetime or None (no lower bound)."""
    q = session.query(Event)
    if model:
        q = q.filter(Event.model == model)
    if event_types:
        q = q.filter(Event.event_type.in_(event_types))
    if since:
        q = q.filter(Event.timestamp >= since)
    return q.order_by(Event.timestamp.desc()).limit(limit).all()


def get_safety_failures(session, since=None, limit=200):
    """safety_check_failed events specifically -- these are always
    REAL_ACTION_EVENT_TYPES (see event.py), i.e. always describe
    something that genuinely went wrong in the live/real-money path,
    never a merely-simulated hiccup."""
    return get_recent_events(session, event_types=["safety_check_failed"], since=since, limit=limit)


def get_trades(session, model=None, is_shadow=None, outcome=None, days_back=None, limit=200):
    q = session.query(Trade)
    if model:
        q = q.filter(Trade.model == model)
    if is_shadow is not None:
        q = q.filter(Trade.is_shadow == is_shadow)
    if outcome:
        q = q.filter(Trade.outcome == outcome)
    if days_back:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = q.filter(Trade.entry_time_utc >= cutoff)
    return q.order_by(Trade.entry_time_ny.desc()).limit(limit).all()


def get_event_chain_for_trade(session, trade: Trade) -> dict:
    """
    Reconstructs the full chain of events (raid -> MSS -> FVG ->
    candidate -> fill -> close, plus any order-manager/real-money
    events) that produced this trade.

    There is no trade_id column on `events` -- this deliberately
    mirrors the exact matching logic shadow_runner/runner.py's own
    _write_trade() uses to find a trade's fill/close events, so the
    dashboard's notion of "which events belong to this trade" never
    diverges from what the system itself considers a match:

      - all events for the same (user, model) on the same NY calendar
        date as the trade's entry
      - within those, the specific order_filled / trade_closed rows
        are the ones matching this trade's direction and
        entry/exit price (see runner.py's own comments on why: several
        candidates can exist the same day, so date alone isn't enough)

    Returns {"day_events": [...], "matched_fill": Event | None,
    "matched_close": Event | None} so the UI can show the whole day's
    activity while highlighting the two events that specifically
    belong to this trade.
    """
    day = trade.entry_time_ny.date()
    day_events = (
        session.query(Event)
        .filter(
            Event.user_id == trade.user_id,
            Event.model == trade.model,
            Event.timestamp >= datetime.combine(day, datetime.min.time()),
            Event.timestamp < datetime.combine(day, datetime.min.time()) + timedelta(days=1),
        )
        .order_by(Event.timestamp.asc())
        .all()
    )

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
    return {"day_events": day_events, "matched_fill": matched_fill, "matched_close": matched_close}


def get_model_configs(session):
    return session.query(ModelConfig).order_by(ModelConfig.model_name).all()


def is_real_action_event(event_type: str) -> bool:
    """True for events that describe something that actually happened
    against the real broker (order placement, fills, safety-check
    failures, ...) -- False for events that describe detection/
    simulation logic only. Used purely for UI coloring."""
    return event_type in REAL_ACTION_EVENT_TYPES
