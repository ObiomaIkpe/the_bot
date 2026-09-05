"""
One-off, run-by-hand script -- historical reconciliation, Piece B
(2026-09-05 design, see misty-seeking-crescent.md's "Historical
reconciliation -- Aug 10 through Sept 4, 2026" section). Fetches every
real broker deal in the window via the new /history/deals bridge
endpoint and correlates them against Piece A's already-replayed
narrative (shadow_runner/historical_reconciliation.py) -- a matched
deal gets a full Trade row, an unmatched one gets journaled honestly
with no fabricated Trade row.

DRY-RUN BY DEFAULT. Per the plan's own mandate, this piece needs a
dedicated live validation pass BEFORE it's trusted to write anything:
1. Confirm mt5.history_deals_get(date_from, date_to)'s real
   inclusive/exclusive boundary behavior -- undocumented, must be
   checked against what this call actually returns live.
2. Cross-check the /history/deals endpoint's raw output against the
   real MT5 terminal / mobile app for this exact account and window.
3. Only once (1) and (2) are confirmed correct, run this script with
   --commit and review its printed output BEFORE trusting the DB
   write it just made.

Run via:
    docker compose run --rm shadow_runner python -m shadow_runner.scripts.reconcile_deals_aug10_sept4_2026
    docker compose run --rm shadow_runner python -m shadow_runner.scripts.reconcile_deals_aug10_sept4_2026 --commit
"""
import argparse
import datetime

from app.core.database import SessionLocal
from app.models import ModelConfig
from shadow_runner.bridge_client import BridgeClient
from shadow_runner.config import ShadowRunnerConfig
from shadow_runner.historical_reconciliation import reconcile_deals
from shadow_runner.persistence import write_event

USER_ID = "d4469ab9-742c-4656-8959-c21602dc71c5"  # same real account as heal_orphans_2026_09_04.py
DATE_FROM = datetime.datetime(2026, 8, 10)
DATE_TO = datetime.datetime(2026, 9, 4, 23, 59, 59)


def main(commit: bool):
    config = ShadowRunnerConfig()
    bridge = BridgeClient(config.bridge_url)
    db = SessionLocal()
    try:
        model_config = db.query(ModelConfig).filter_by(user_id=USER_ID, model_name=config.model).one()
        magic = model_config.magic_number
        risk_pct = model_config.risk_pct

        print(f"Fetching deals {DATE_FROM} -> {DATE_TO} ({config.symbol}, magic={magic})...")
        deals = bridge.get_deals_history(DATE_FROM, DATE_TO)
        print(f"Bridge returned {len(deals)} raw deals (all symbols/magics on this account).")

        events = reconcile_deals(
            db, bridge, deals, magic=magic, user_id=USER_ID, model=config.model,
            risk_pct=risk_pct, symbol=config.symbol,
        )
        print(f"Reconciliation produced {len(events)} event(s):")
        for e in events:
            print(" ", e)

        if not commit:
            print("\nDRY RUN -- rolling back, nothing written. Re-run with --commit to actually write.")
            db.rollback()
            return

        for e in events:
            write_event(db, e, USER_ID, config.model)
        db.commit()
        print(f"\nCOMMITTED -- {len(events)} event(s) written.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Actually write to the DB. Default is dry-run.")
    args = parser.parse_args()
    main(commit=args.commit)
