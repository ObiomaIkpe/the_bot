"""
Writes shadow-mode events and trades to Postgres, via the same
SQLAlchemy models/session the rest of the app uses (app.core.database,
app.models). No new tables, no new ORM layer -- this reuses exactly what
Phase 0 already built.
"""
import logging
import uuid

from sqlalchemy.orm import Session

from app.models import Event, Trade

log = logging.getLogger("shadow_runner.persistence")


def write_event(db: Session, event: dict, user_id: str, model: str) -> Event:
    """
    Converts a streaming-component event dict (from DayOrchestrator's
    event_sink, or DaySelectionGate's on_day_closed/gate_for_day) into an
    Event row. event_type and timestamp are lifted out to their own
    columns; everything else in the dict goes into `details` (JSONB) --
    this is deliberately generic so new event fields never require a
    schema change, only VALID_EVENT_TYPES additions when a genuinely new
    event *type* appears.
    """
    event = dict(event)  # don't mutate the caller's dict
    event_type = event.pop("event_type")
    timestamp = event.pop("timestamp")

    row = Event(
        event_id=uuid.uuid4(),
        user_id=user_id,
        model=model,
        event_type=event_type,
        timestamp=timestamp,
        details=event,  # whatever's left (direction, price, bar_index, etc.)
        is_shadow=True,  # Phase 3: always True. Nothing sets this False
                          # until Phase 4 ships real order placement.
    )
    db.add(row)
    return row


def compute_realized_r(trade: dict) -> float:
    """
    R-multiple = (actual price movement) / (planned risk distance).
    Deliberately NOT assuming a fixed reward:risk ratio (e.g. hardcoded
    2.0) -- the locked model's target is the dynamic candle-midpoint
    calculation (see TradeAttempt), which doesn't produce a constant
    R on wins. Computing it this way is correct regardless of what the
    target logic did, including for scratches (which land at whatever
    price the day happened to close at, producing a fractional R rather
    than a clean 0).
    """
    risk = abs(trade["entry"] - trade["stop"])
    if risk == 0:
        return 0.0  # shouldn't happen (MIN_STOP_PIPS rejection prevents
                     # this upstream), but never divide by zero
    if trade["direction"] == "long":
        pnl = trade["exit_price"] - trade["entry"]
    else:
        pnl = trade["entry"] - trade["exit_price"]
    return pnl / risk


def get_current_equity(db: Session, user_id: str, model: str, bridge_starting_equity: float) -> float:
    """
    Resume point for equity tracking: use the most recent Trade's
    equity_after for this (user, model) if one exists, otherwise seed
    from the real broker demo balance (bridge_starting_equity, passed in
    by the caller -- see runner.py). This is also, incidentally, most of
    what Phase 3 step 6 (mid-day-restart recovery) needs for equity
    specifically: a restarted runner picks up exactly where the last
    committed trade left off, rather than re-seeding from the broker
    balance every time (which would double-count any trades already
    journaled).
    """
    last_trade = (
        db.query(Trade)
        .filter(Trade.user_id == user_id, Trade.model == model, Trade.is_shadow.is_(True))
        .order_by(Trade.entry_time_utc.desc())
        .first()
    )
    if last_trade is not None and last_trade.equity_after is not None:
        return last_trade.equity_after
    return bridge_starting_equity


def write_trade(
    db: Session,
    trade: dict,
    entry_time_utc,
    entry_time_ny,
    exit_time_utc,
    user_id: str,
    model: str,
    risk_pct: float,
    equity_before: float,
    setup_context: dict,
) -> Trade:
    """
    trade: the dict DayOrchestrator.finalize() returns
        ({"direction", "entry", "stop", "target", "risk_pips", "outcome", "exit_price"}).
    entry_time_utc/entry_time_ny: recovered by the caller from the day's
        already-journaled order_filled event (finalize() itself doesn't
        carry a timestamp for when the winning attempt actually filled).
    """
    realized_r = compute_realized_r(trade)
    equity_after = equity_before + (equity_before * risk_pct * realized_r)

    row = Trade(
        trade_id=uuid.uuid4(),
        user_id=user_id,
        model=model,
        is_shadow=True,
        direction=trade["direction"],
        entry_price=trade["entry"],
        stop_price=trade["stop"],
        target_price=trade["target"],
        exit_price=trade["exit_price"],
        outcome=trade["outcome"],
        realized_r=realized_r,
        entry_time_utc=entry_time_utc,
        entry_time_ny=entry_time_ny,
        exit_time_utc=exit_time_utc,
        risk_pct_used=risk_pct,
        equity_before=equity_before,
        equity_after=equity_after,
        setup_context=setup_context,
    )
    db.add(row)
    return row