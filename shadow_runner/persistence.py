"""
Writes shadow-mode events and trades to Postgres, via the same
SQLAlchemy models/session the rest of the app uses (app.core.database,
app.models). No new tables, no new ORM layer -- this reuses exactly what
Phase 0 already built.
"""
import logging
import uuid

from sqlalchemy.orm import Session

from app.models import Event, ModelConfig, Trade

log = logging.getLogger("shadow_runner.persistence")


def event_type_exists(db: Session, user_id: str, model: str, event_type: str) -> bool:
    """
    Phase 3 step 7 (cold-start trend bootstrap). Cheap existence check --
    used both to detect the one-time bootstrap marker
    (trend_history_bootstrapped) and, separately, to detect any
    pre-existing REAL daily_swing_*_confirmed events (guards against
    re-bootstrapping a system that's already been running for real --
    see runner.py's _bootstrap_trend_history_if_needed()).
    """
    row = (
        db.query(Event)
        .filter(Event.user_id == user_id, Event.model == model, Event.event_type == event_type)
        .first()
    )
    return row is not None


def get_recent_swing_history(db: Session, user_id: str, model: str) -> tuple[list, list]:
    """
    Phase 3 step 6 (restart recovery). Returns (confirmed_highs,
    confirmed_lows) -- each a list of up to 2 (day_index, price) tuples,
    oldest first, matching DaySelectionGate._confirmed_highs/_lows'
    exact shape -- reconstructed from the last 2 daily_swing_high_confirmed
    and last 2 daily_swing_low_confirmed events already in the database
    for this (user, model). day_index is synthesized as a simple
    increasing placeholder (0, 1) since only relative order and price
    matter to DaySelectionGate.seed_trend_history() -- see that method's
    docstring for why the real historical day_index values aren't needed.

    Returns ([], []) if fewer than 2 of either type exist yet (e.g. a
    brand-new deployment with no history at all) -- the gate will
    correctly report "no_trend" until enough real days accumulate, same
    as a fresh start.
    """
    highs = (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.model == model,
            Event.event_type == "daily_swing_high_confirmed",
        )
        .order_by(Event.timestamp.desc())
        .limit(2)
        .all()
    )
    lows = (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.model == model,
            Event.event_type == "daily_swing_low_confirmed",
        )
        .order_by(Event.timestamp.desc())
        .limit(2)
        .all()
    )
    # DB order is newest-first (for LIMIT to grab the right 2); the gate
    # needs oldest-first so [-2]/[-1] indexing lands on the right pair.
    highs = list(reversed(highs))
    lows = list(reversed(lows))
    confirmed_highs = [(i, e.details["price"]) for i, e in enumerate(highs)]
    confirmed_lows = [(i, e.details["price"]) for i, e in enumerate(lows)]
    return confirmed_highs, confirmed_lows


def get_last_event_timestamp_for_date(db: Session, user_id: str, model: str, date) -> object | None:
    """
    Phase 3 step 6 (restart recovery). Returns the latest event
    timestamp already journaled for this (user, model) on the given NY
    calendar date, or None if nothing has been journaled for that date
    yet. Used to decide whether it's safe to replay today's bars
    (nothing written yet -> safe, no duplicate risk) or whether a
    restart happened mid-day after partial progress was already
    committed (unsafe to replay -- would duplicate everything before the
    crash point; see runner.py's recover_on_startup() for how this
    result gets used).

    NOTE: filters in Python on date, not via a DB-side date range query
    -- acceptable for now since one day's event volume is small (at most
    a few hundred rows), but worth revisiting with a proper WHERE
    timestamp::date = :date if this ever needs to scale to many users.
    """
    todays_events = (
        db.query(Event)
        .filter(Event.user_id == user_id, Event.model == model)
        .order_by(Event.timestamp.desc())
        .limit(500)  # generous cap -- see NOTE above
        .all()
    )
    for e in todays_events:
        if e.event_type == "trend_history_bootstrapped":
            # Bookkeeping marker, not real trading activity -- excluded
            # so the bootstrap step itself never looks like "today
            # already has journaled events" on the very run it just
            # wrote that marker on. Real bug caught in live testing:
            # without this, every cold start would immediately think
            # today was already partially processed and skip replay.
            continue
        # e.timestamp is NY wall-clock (that's what gets fed in as
        # `timestamp` throughout the streaming components -- see
        # write_event()'s docstring).
        if e.timestamp.date() == date:
            return e.timestamp
    return None


def get_model_config(db: Session, user_id: str, model_name: str) -> dict | None:
    """
    Phase 4 step 2c. Fetches the real (status, risk_pct, magic_number)
    for one (user, model) from the model_configs table -- replaces the
    old assumption that a model's risk_pct lived on UserSettings (a
    Phase 0/3-era, single-model idea; UserSettings.risk_pct is now
    stale/unused going forward, see runner.py's _write_trade()).
    Returns None if no row exists for this (user, model) -- callers
    should treat that as "this model has no config at all yet," not the
    same as status='disabled' (which means a row exists but is
    intentionally off).
    """
    row = (
        db.query(ModelConfig)
        .filter(ModelConfig.user_id == user_id, ModelConfig.model_name == model_name)
        .first()
    )
    if row is None:
        return None
    return {
        "model_name": row.model_name,
        "status": row.status,
        "risk_pct": row.risk_pct,
        "magic_number": row.magic_number,
    }


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