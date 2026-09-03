"""
Reconstructs a trade's full reasoning chain (raid -> MSS -> FVG ->
candidate -> fill -> close) by walking the bar-index cross-references
each detector already writes into its own event's `details` -- see
phase1/streaming/raid_detector.py, mss_watch.py, fvg_detector.py,
day_orchestrator.py, and trade_attempt.py for where each of these
fields actually gets set. Entirely a read-time correlation over data
that already exists: no new instrumentation, no schema change, no
shadow_runner write-path change.

Deliberately NOT memoized/cached beyond one request -- trade detail
pages are low-traffic, and correctness (always reading current DB
state) matters more than shaving one query here.
"""
import dataclasses
import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.trade import Trade

_FLOAT_TOLERANCE = 1e-9


@dataclasses.dataclass
class TradeChainResult:
    # Ordered oldest -> newest: as much of [raid, mss, fvg, candidate,
    # fill, close] as could be resolved. Never includes events from
    # OTHER candidates that happened the same day -- that's the whole
    # point of walking the chain instead of just showing the day.
    chain: list[Event]
    # True only when every stage through the fill resolved (a missing
    # close is still "fully resolved" -- an open/scratch trade
    # legitimately has no close event to find). False means the caller
    # should tell the user this trade's story couldn't be fully
    # reconstructed, not silently show a partial/misleading chain as
    # if it were complete.
    fully_resolved: bool


def _same_day_events(db: Session, trade: Trade) -> list[Event]:
    """
    Multi-user fan-out, piece 1.5: the raid/MSS/FVG/candidate/simulated-
    fill-and-close chain this walks is all NARRATIVE_EVENT_TYPES now --
    shared, ownerless (user_id IS NULL), not this trade's owner's
    personal events. Includes both: this trade's own real-action events
    (user_id == trade.user_id, e.g. a real fill/close if one happened)
    AND the shared narrative for that model/day, so the chain-walking
    below still finds everything it did before this trade's owner had
    to "own" the whole day's story.
    """
    day = trade.entry_time_ny.date()
    day_start = datetime.datetime.combine(day, datetime.time.min)
    day_end = day_start + datetime.timedelta(days=1)
    return (
        db.query(Event)
        .filter(
            or_(Event.user_id == trade.user_id, Event.user_id.is_(None)),
            Event.model == trade.model,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
        .order_by(Event.timestamp.asc())
        .all()
    )


def _floats_match(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) < _FLOAT_TOLERANCE


def _find_fill(events: list[Event], trade: Trade) -> Event | None:
    # Prefer the real FK (set directly by shadow_runner/runner.py's
    # _write_trade() for any trade written after migration 0017) --
    # exact, no heuristic needed.
    fk_match = next((e for e in events if e.trade_id == trade.trade_id and e.event_type == "order_filled"), None)
    if fk_match is not None:
        return fk_match
    # Fall back to the same direction+price heuristic
    # app/routers/admin.py's event-chain endpoint already uses, for
    # older/un-backfilled rows -- means this works for every historical
    # trade too, no dependency on app/scripts/backfill_event_trade_ids.py
    # having been run.
    return next(
        (
            e for e in events
            if e.event_type == "order_filled"
            and e.details.get("direction") == trade.direction
            and _floats_match(e.details.get("entry"), trade.entry_price)
        ),
        None,
    )


def _find_close(events: list[Event], trade: Trade) -> Event | None:
    fk_match = next((e for e in events if e.trade_id == trade.trade_id and e.event_type == "trade_closed"), None)
    if fk_match is not None:
        return fk_match
    return next(
        (
            e for e in reversed(events)
            if e.event_type == "trade_closed"
            and e.details.get("outcome") == trade.outcome
            and _floats_match(e.details.get("exit_price"), trade.exit_price)
        ),
        None,
    )


def _find_candidate(events: list[Event], fill: Event) -> Event | None:
    # trade_candidate_ready is what TradeAttempt (and therefore this
    # fill) was constructed from -- direction/entry/stop match exactly.
    return next(
        (
            e for e in events
            if e.event_type == "trade_candidate_ready"
            and e.details.get("direction") == fill.details.get("direction")
            and _floats_match(e.details.get("entry"), fill.details.get("entry"))
            and _floats_match(e.details.get("stop"), fill.details.get("stop"))
        ),
        None,
    )


def _find_mss(events: list[Event], candidate: Event) -> Event | None:
    raid_bar = candidate.details.get("raid_bar")
    mss_bar = candidate.details.get("mss_bar")
    return next(
        (
            e for e in events
            if e.event_type == "mss_confirmed"
            and e.details.get("raid_bar_index") == raid_bar
            and e.details.get("mss_bar_index") == mss_bar
        ),
        None,
    )


def _find_raid(events: list[Event], mss: Event) -> Event | None:
    return next(
        (e for e in events if e.event_type == "raid_detected" and e.details.get("bar_index") == mss.details.get("raid_bar_index")),
        None,
    )


def _find_fvg(events: list[Event], mss: Event) -> Event | None:
    return next(
        (e for e in events if e.event_type == "fvg_found" and e.details.get("mss_bar_index") == mss.details.get("mss_bar_index")),
        None,
    )


def build_trade_chain(db: Session, trade: Trade) -> TradeChainResult:
    events = _same_day_events(db, trade)

    fill = _find_fill(events, trade)
    if fill is None:
        return TradeChainResult(chain=[], fully_resolved=False)

    close = _find_close(events, trade)
    candidate = _find_candidate(events, fill)
    if candidate is None:
        return TradeChainResult(chain=[e for e in (fill, close) if e is not None], fully_resolved=False)

    mss = _find_mss(events, candidate)
    raid = _find_raid(events, mss) if mss is not None else None
    fvg = _find_fvg(events, mss) if mss is not None else None

    chain = [e for e in (raid, mss, fvg, candidate, fill, close) if e is not None]
    fully_resolved = raid is not None and mss is not None and fvg is not None
    return TradeChainResult(chain=chain, fully_resolved=fully_resolved)
