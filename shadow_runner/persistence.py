"""
Writes shadow-mode events and trades to Postgres, via the same
SQLAlchemy models/session the rest of the app uses (app.core.database,
app.models). No new tables, no new ORM layer -- this reuses exactly what
Phase 0 already built.
"""
import logging
import uuid

from sqlalchemy.orm import Session

from app.models import Event, ModelConfig, REAL_ACTION_EVENT_TYPES, Trade, UserSettings

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
        "is_paused": row.is_paused,
    }


def get_user_paused_status(db: Session, user_id: str) -> bool:
    """
    Phase 4 step 4 (safety rails). Account-wide emergency stop --
    distinct from ModelConfig.status, which is a per-model, more
    deliberate switch. This is meant to be "stop everything for this
    user right now," so it's ALWAYS fetched fresh (see
    OrderManager.on_trade_candidate_ready()'s safety-rail check) --
    never cached at startup the way model_config is, since a pause
    that only takes effect after a restart defeats the point.

    Returns False (not paused) if somehow no UserSettings row exists --
    matches this project's established "fail toward the safer, more
    conservative interpretation only where that's actually safer" isn't
    quite right here; failing OPEN (not paused) when settings are
    missing is a genuine judgment call, flagged rather than silently
    assumed -- a missing UserSettings row is itself an anomaly worth
    investigating, not a signal to block trading. Logged by the caller,
    not here.
    """
    row = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if row is None:
        return False
    return row.is_paused


def get_model_paused_status(db: Session, user_id: str, model_name: str) -> bool:
    """
    Per-model pause -- distinct from get_user_paused_status() (the
    account-wide emergency stop). Same fetched-fresh-every-call
    discipline and the same fail-toward-not-paused-when-missing
    convention: a missing model_configs row is itself an anomaly
    (get_model_config() already returns None for that case, logged by
    the caller), not a signal to block trading.
    """
    row = (
        db.query(ModelConfig)
        .filter(ModelConfig.user_id == user_id, ModelConfig.model_name == model_name)
        .first()
    )
    if row is None:
        return False
    return row.is_paused


def get_max_daily_loss_pct(db: Session, user_id: str) -> float | None:
    """
    Phase 4 step 4 Part 2 (visibility only -- confirmed design: this
    number is journaled for awareness, it does NOT block new trades or
    force-close anything, see OrderManager.check_daily_loss_threshold()).
    Returns None if no UserSettings row exists.
    """
    row = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if row is None:
        return None
    return row.max_daily_loss_pct


def get_realized_pnl_today(db: Session, user_id: str, model: str, today_date) -> float:
    """
    Phase 4 step 4 Part 2. Sum of REAL, REALIZED profit (real_profit)
    across every trade for this (user, model) that fully closed today
    (real_status == 'closed', real_close_time_ny's date matches
    today_date). Net P&L, not "sum of losses only" -- a day with one
    big loss partly offset by smaller wins is a losing day; a day with
    one loss fully offset by a win isn't.

    Deliberately does NOT include partial_close_profit -- that field is
    currently always None (MT5's order_send result doesn't return
    realized profit directly for a partial close the way
    history_deals_get does for a full close; see
    position_tracker.py's _do_partial_close() for the same documented
    gap). A position that's only been partially closed today therefore
    doesn't contribute to today's realized total via this function at
    all yet -- a known, honest limitation, not silently guessed at.

    Filters by date in Python, not SQL, matching the same established
    pattern (and the same documented scaling caveat) as
    get_last_event_timestamp_for_date().
    """
    rows = (
        db.query(Trade)
        .filter(
            Trade.user_id == user_id, Trade.model == model,
            Trade.is_shadow.is_(False), Trade.real_status == "closed",
        )
        .all()
    )
    total = 0.0
    for r in rows:
        if r.real_close_time_ny is not None and r.real_close_time_ny.date() == today_date:
            total += (r.real_profit or 0.0)
    return total


def write_event(db: Session, event: dict, user_id: str, model: str) -> Event:
    """
    Converts a streaming-component event dict (from DayOrchestrator's
    event_sink, DaySelectionGate's on_day_closed/gate_for_day, or
    OrderManager's own event_sink) into an Event row. event_type and
    timestamp are lifted out to their own columns; everything else in
    the dict goes into `details` (JSONB) -- this is deliberately generic
    so new event fields never require a schema change, only
    VALID_EVENT_TYPES additions when a genuinely new event *type*
    appears.

    is_shadow is derived from event_type via REAL_ACTION_EVENT_TYPES
    (app/models/event.py) -- NOT hardcoded. Fixes a real, stale bug:
    this used to be unconditionally True on every row (correct back
    when only DayOrchestrator/DaySelectionGate ever emitted events;
    wrong once OrderManager started emitting real-action events in
    Phase 4). See REAL_ACTION_EVENT_TYPES's own comment for the full
    reasoning on why this is decidable from event_type alone.
    """
    event = dict(event)  # don't mutate the caller's dict
    event_type = event.pop("event_type")
    timestamp = event.pop("timestamp")
    is_shadow = event_type not in REAL_ACTION_EVENT_TYPES

    row = Event(
        event_id=uuid.uuid4(),
        user_id=user_id,
        model=model,
        event_type=event_type,
        timestamp=timestamp,
        details=event,  # whatever's left (direction, price, bar_index, etc.)
        is_shadow=is_shadow,
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

    Phase 4 fix: no longer filters on is_shadow. The original filter
    (is_shadow.is_(True)) silently meant this query would NEVER find a
    real (is_shadow=False) trade once one existed -- the equity chain
    would appear frozen at whatever it was the moment a model first
    went 'active', always falling back to bridge_starting_equity instead
    of continuing to compound. Removing the filter keeps the actual
    equity_after computation exactly as it's always been (based on the
    trade's simulated realized_r, unchanged by this fix) -- it only
    fixes which ROW gets found as "most recent," not what equity means.
    Whether equity tracking should reflect the SIMULATED or REAL P&L for
    an is_shadow=False trade is a separate, not-yet-decided question --
    flagged, not resolved, here.
    """
    last_trade = (
        db.query(Trade)
        .filter(Trade.user_id == user_id, Trade.model == model)
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
    is_shadow: bool = True,
    real_outcome: dict | None = None,
) -> Trade:
    """
    trade: the dict DayOrchestrator.finalize() returns
        ({"direction", "entry", "stop", "target", "risk_pips", "outcome", "exit_price"}).
    entry_time_utc/entry_time_ny: recovered by the caller from the day's
        already-journaled order_filled event (finalize() itself doesn't
        carry a timestamp for when the winning attempt actually filled).
    is_shadow: Phase 4 addition. Defaults to True (safe default, matches
        all pre-Phase-4 behavior) -- pass False only when this model's
        status was genuinely 'active' for this trade, i.e. a real order
        was actually placed.
    real_outcome: Phase 4 step 3 addition. dict from
        OrderManager.get_real_outcome(), or None if no real order was
        placed today (e.g. shadow/disabled model, or an active model
        that simply had no fill today). When provided, populates the
        real_* columns alongside the always-present simulated columns --
        see app/models/trade.py's module comment on why both live on
        the same row.
    """
    realized_r = compute_realized_r(trade)
    equity_after = equity_before + (equity_before * risk_pct * realized_r)

    row = Trade(
        trade_id=uuid.uuid4(),
        user_id=user_id,
        model=model,
        is_shadow=is_shadow,
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
        real_position_ticket=real_outcome["position_ticket"] if real_outcome else None,
        real_fill_price=real_outcome["fill_price"] if real_outcome else None,
        real_fill_time_utc=real_outcome["fill_time_utc"] if real_outcome else None,
        real_fill_time_ny=real_outcome["fill_time_ny"] if real_outcome else None,
        real_close_price=real_outcome["close_price"] if real_outcome else None,
        real_close_time_utc=real_outcome["close_time_utc"] if real_outcome else None,
        real_close_time_ny=real_outcome["close_time_ny"] if real_outcome else None,
        real_profit=real_outcome["profit"] if real_outcome else None,
        real_close_reason=real_outcome["close_reason"] if real_outcome else None,
        # Phase 4 overnight-position handling: 'open' the moment a real
        # fill exists but hasn't closed yet (the common case -- a real
        # position rarely resolves within the same poll cycle it's
        # written on) -- PositionTracker takes over from here, across
        # however many days it takes. 'closed' in the rare case it
        # somehow already resolved by write time. None for shadow trades.
        real_status=(
            None if real_outcome is None
            else ("closed" if real_outcome["close_price"] is not None else "open")
        ),
    )
    db.add(row)
    return row


def get_open_real_trades(db: Session, user_id: str, model: str) -> list[dict]:
    """
    Phase 4 overnight-position handling. Returns every trade still
    real_status IN ('open', 'partial_closed') for this (user, model) --
    used once at startup to rebuild PositionTracker's in-memory state,
    since a real position can now legitimately span a runner restart or
    multiple days (see shadow_runner/position_tracker.py).
    """
    rows = (
        db.query(Trade)
        .filter(
            Trade.user_id == user_id, Trade.model == model,
            Trade.real_status.in_(["open", "partial_closed"]),
        )
        .all()
    )
    return [
        {
            "trade_id": r.trade_id,
            "real_position_ticket": r.real_position_ticket,
            "real_status": r.real_status,
            "entry_time_ny": r.entry_time_ny,
            "direction": r.direction,
        }
        for r in rows
    ]


def update_trade_partial_close(
    db: Session, trade_id, partial_close_price: float, partial_close_time_utc,
    partial_close_time_ny, partial_close_volume: float, partial_close_profit: float,
) -> None:
    """Phase 4 overnight-position handling. Caller commits."""
    row = db.query(Trade).filter(Trade.trade_id == trade_id).one()
    row.real_status = "partial_closed"
    row.partial_close_price = partial_close_price
    row.partial_close_time_utc = partial_close_time_utc
    row.partial_close_time_ny = partial_close_time_ny
    row.partial_close_volume = partial_close_volume
    row.partial_close_profit = partial_close_profit


def update_trade_final_close(
    db: Session, trade_id, close_price: float, close_time_utc, close_time_ny,
    profit: float, close_reason: str,
) -> None:
    """Phase 4 overnight-position handling. Caller commits. Works
    whether the trade was ever partially closed or not -- real_close_*
    always describes the FINAL resolution regardless of path."""
    row = db.query(Trade).filter(Trade.trade_id == trade_id).one()
    row.real_status = "closed"
    row.real_close_price = close_price
    row.real_close_time_utc = close_time_utc
    row.real_close_time_ny = close_time_ny
    row.real_profit = profit
    row.real_close_reason = close_reason