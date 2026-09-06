"""
One-off, run-by-hand script -- backfills `real_volume` (the broker-
filled lot size) onto real trades recorded before migration 0023
(2026-09-06) added the column. Forward capture was wired into all
three write paths the same day (OrderManager._on_fill()/
get_real_outcome(), persistence.write_orphan_trade(),
persistence.write_reconciled_historical_trade()) -- every trade from
today onward gets real_volume automatically, with no further action
needed. This script only exists to fill in the gap for rows that were
already written before that.

Reuses the bridge's already-live /history/deals endpoint (built for
historical reconciliation Piece B, 2026-09-05) -- no new bridge
capability needed. For each user with a real trade missing
real_volume, fetches that account's deal history across the window
covering their affected trades' entry times, and reads the lot size
straight off each position's own entry ("in") deal -- the same field
(bridge/app/mt5_client.py's Deal.volume) reconcile_deals() already
uses, just applied to trades that predate this column rather than
newly-reconciled ones.

Deliberately narrower in scope than historical_reconciliation.py's
reconcile_deals(): this only ever needs the ENTRY deal (present the
instant a position fills, whether it's since closed or is still
genuinely open) -- unlike reconcile_deals(), it does not require a
matching "out" deal, so a still-open real trade gets backfilled here
too, not just closed ones.

Following this project's established convention for one-off scripts
(see test_backfill_narrative_script.py's own docstring): main() itself
(real SessionLocal, real BridgeClient) is not tested directly --
apply_volume_backfill() below, the actual matching logic, is extracted
so it can be unit tested with plain fixtures instead.

DRY-RUN BY DEFAULT, same discipline as every other script in this
directory. Only ever UPDATEs real_volume -- touches no other column.

Idempotent: only ever selects rows where real_volume IS NULL, so
re-running after a partial run, or after new trades have accumulated
since, is always safe -- nothing gets double-applied or overwritten.

Run via:
    docker compose run --rm shadow_runner python -m shadow_runner.scripts.backfill_real_volume_2026_09_06
    docker compose run --rm shadow_runner python -m shadow_runner.scripts.backfill_real_volume_2026_09_06 --commit
"""
import argparse
import datetime
import uuid

from app.core.database import SessionLocal
from app.models import BrokerCredential, Trade
from shadow_runner.bridge_client import BridgeClient
from shadow_runner.historical_reconciliation import _group_deals_by_position

# Generous margin around the affected trades' own entry-time window --
# a deals-history read is cheap, no reason to cut this close and risk
# missing a deal right at the boundary.
_MARGIN = datetime.timedelta(days=1)


def apply_volume_backfill(trades: list[Trade], deals: list[dict]) -> tuple[int, list[uuid.UUID]]:
    """
    Pure matching logic, no DB/bridge I/O -- mutates `t.real_volume` in
    place on each trade whose ticket has a matching entry ("in") deal.
    Returns (updated_count, [trade_id for every trade left unmatched]).

    Only ever needs the entry deal -- unlike reconcile_deals(), does
    NOT require a corresponding "out" deal, so a still-open real trade
    (real_status='open') gets matched here too, not just closed ones.
    """
    by_position = _group_deals_by_position(deals)
    updated = 0
    unmatched = []
    for t in trades:
        position_deals = by_position.get(t.real_position_ticket)
        in_deals = [d for d in position_deals if d["entry"] == "in"] if position_deals else []
        if not in_deals:
            unmatched.append(t.trade_id)
            continue
        t.real_volume = in_deals[0]["volume"]
        updated += 1
    return updated, unmatched


def main(commit: bool):
    db = SessionLocal()
    try:
        missing = (
            db.query(Trade)
            .filter(Trade.real_position_ticket.isnot(None), Trade.real_volume.is_(None))
            .all()
        )
        if not missing:
            print("Nothing to backfill -- every real trade already has real_volume.")
            return

        print(f"{len(missing)} real trade(s) missing real_volume.")

        by_user: dict = {}
        for t in missing:
            by_user.setdefault(t.user_id, []).append(t)

        total_updated = 0
        total_unmatched: list[uuid.UUID] = []

        for user_id, trades in by_user.items():
            cred = db.query(BrokerCredential).filter_by(user_id=user_id, is_active=True).first()
            if cred is None:
                print(f"  user_id={user_id}: no active broker credential -- skipping {len(trades)} trade(s)")
                total_unmatched.extend(t.trade_id for t in trades)
                continue

            bridge = BridgeClient(cred.bridge_url)
            date_from = min(t.entry_time_utc for t in trades) - _MARGIN
            date_to = max(t.entry_time_utc for t in trades) + _MARGIN

            print(
                f"  user_id={user_id}: fetching deals {date_from.date()} -> {date_to.date()} "
                f"({len(trades)} trade(s) to match, bridge={cred.bridge_url})..."
            )
            deals = bridge.get_deals_history(date_from, date_to)
            updated, unmatched = apply_volume_backfill(trades, deals)
            for t in trades:
                if t.trade_id not in unmatched:
                    print(f"    trade {t.trade_id} (ticket {t.real_position_ticket}): real_volume -> {t.real_volume}")
            for tid in unmatched:
                print(f"    trade {tid}: no entry deal found in this window -- skipping")
            total_updated += updated
            total_unmatched.extend(unmatched)

        if not commit:
            print(f"\nDRY RUN -- would update {total_updated} row(s), {len(total_unmatched)} unmatched. "
                  f"Re-run with --commit to actually write.")
            db.rollback()
            return

        db.commit()
        print(f"\nCOMMITTED -- {total_updated} row(s) updated, {len(total_unmatched)} unmatched.")
        if total_unmatched:
            print("Unmatched trade_ids (left untouched, safe to investigate/re-run later):")
            for tid in total_unmatched:
                print(" ", tid)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Actually write to the DB. Default is dry-run.")
    args = parser.parse_args()
    main(commit=args.commit)
