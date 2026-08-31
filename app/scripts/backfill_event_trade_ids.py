"""
One-time (but safe to re-run) backfill: sets events.trade_id on
historical rows written before that column existed (migration 0017).
Going forward, shadow_runner/runner.py's _write_trade() sets this
directly at write time -- this script only ever needs to touch rows
from before that.

Reuses the exact same matching heuristic app/routers/admin.py's
get_trade_event_chain() falls back to for un-backfilled rows: same
(user, model) + NY calendar date as the trade, then the specific
order_filled/trade_closed rows matching direction and entry/exit price.
Only ever sets trade_id where it's currently NULL -- never overwrites
an already-linked row, so this is safe to re-run any time (e.g. after
new historical trades are somehow discovered) without redoing work.

Run manually:
    python -m app.scripts.backfill_event_trade_ids
"""
import datetime

from app.core.database import SessionLocal
from app.models.event import Event
from app.models.trade import Trade


def main() -> None:
    db = SessionLocal()
    try:
        trades = db.query(Trade).order_by(Trade.entry_time_ny.asc()).all()
        linked = 0
        for trade in trades:
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
                    Event.trade_id.is_(None),
                )
                .order_by(Event.timestamp.asc())
                .all()
            )

            fill = next(
                (
                    e for e in day_events
                    if e.event_type == "order_filled"
                    and e.details.get("direction") == trade.direction
                    and e.details.get("entry") is not None
                    and abs(e.details["entry"] - trade.entry_price) < 1e-9
                ),
                None,
            )
            close = next(
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

            for event in (fill, close):
                if event is not None:
                    event.trade_id = trade.trade_id
                    linked += 1

        db.commit()
        print(f"Done -- checked {len(trades)} trade(s), linked {linked} event(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
