"""
One-off, run-by-hand diagnostic script -- 2026-09-05, historical
reconciliation Piece B. The dry-run of reconcile_deals_aug10_sept4_2026.py
reported the Aug 27/28 sibling-race trades (a real, already-known
incident -- see two_real_bugs_found_sept2 in the project's own notes)
as "matched: False", even though a direct query confirmed a
trade_candidate_ready event exists on 2026-08-27 with the exact same
entry price (1.1646) and direction (long) the real deal has. This
script queries the real production DB directly to find out exactly
why _find_matching_candidate_event() isn't finding it -- printing the
real timestamp type/value/date() result rather than guessing.

Safe to run any number of times -- read-only, no writes.

Run via: docker compose run --rm shadow_runner python -m shadow_runner.scripts.diagnose_candidate_match_2026_09_05
"""
import datetime

from app.core.database import SessionLocal
from app.models import Event
from shadow_runner.historical_reconciliation import _find_matching_candidate_event


def main():
    db = SessionLocal()
    try:
        entry_price = 1.1646
        direction = "long"
        entry_date = datetime.date(2026, 8, 27)

        result = _find_matching_candidate_event(db, "fvg", direction, entry_price, entry_date)
        print("MATCH RESULT:", result)

        candidates = (
            db.query(Event)
            .filter(Event.event_type == "trade_candidate_ready", Event.model == "fvg", Event.user_id.is_(None))
            .all()
        )
        print("total candidates in table:", len(candidates))
        for e in candidates:
            print(
                " ", e.event_id,
                "timestamp=", repr(e.timestamp),
                "date()=", e.timestamp.date(),
                "matches entry_date?", e.timestamp.date() == entry_date,
                "details=", e.details,
                "user_id=", e.user_id,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
