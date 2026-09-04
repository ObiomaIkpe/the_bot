"""
One-off, run-by-hand script -- 2026-09-04 orphan-recovery incident.

Tonight's fan-out deploy applied migrations 0020/0021 and finally
shipped bug 2's orphan-recovery fix (built 2026-09-02, never deployed
until tonight). Its first-ever real run found two genuinely orphaned
positions (a sibling-fill race from 2026-09-02, invisible until
tonight) with no take-profit attached -- and the heal itself crashed on
a real, separate, pre-existing bug (BridgeClient.get_positions()
returns raw string timestamps, not parsed datetimes; see the fix in
shadow_runner/orphan_recovery.py, same commit as this script).

A plain restart won't re-trigger the orphan check (it only runs when a
cross-day gap is detected, and that gap was already consumed by an
earlier restart tonight) -- this script calls the exact same
production function directly, once, targeted at the real account.
Safe to run any number of times: it only ever acts on positions the
bridge reports as still genuinely open with no matching `trades` row;
anything already closed or already tracked is silently skipped.

Run via: docker compose run --rm shadow_runner python -m shadow_runner.scripts.heal_orphans_2026_09_04
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from shadow_runner.bridge_client import BridgeClient
from shadow_runner.orphan_recovery import check_for_orphaned_positions
from shadow_runner.persistence import write_event

BRIDGE_URL = "http://38.247.137.208:8001"
USER_ID = "d4469ab9-742c-4656-8959-c21602dc71c5"
MODEL = "fvg"
SYMBOL = "EURUSDm"
MAGIC = 900001


def main():
    bridge = BridgeClient(BRIDGE_URL)
    db = SessionLocal()
    try:
        now_ny = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)

        collected = []
        results = check_for_orphaned_positions(
            bridge, SYMBOL, MAGIC, db, USER_ID, MODEL, now_ny, collected.append,
        )
        print("RESULTS:", results)
        for e in collected:
            print("EVENT:", e)
            write_event(db, dict(e), USER_ID, MODEL)
        db.commit()
        print("DONE")
    finally:
        db.close()


if __name__ == "__main__":
    main()
