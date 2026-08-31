"""events: add trade_id

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-31

Logging/audit review, part 3: persists the trade<->event link that
shadow_runner/runner.py's _write_trade() already computes in memory
every time (matching by direction/price within the trade's NY calendar
date) but never wrote back to the DB -- consumers like the admin
event-chain endpoint had to re-derive the same heuristic match
themselves. Nullable + no backfill here: most existing events (swing
detection, day-skip reasons, safety checks) were never trade-matched
to begin with, and a data backfill for historical trade-matched events
is a separate, deliberate script (not bundled into a schema
migration), see app/scripts/backfill_event_trade_ids.py.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_events_trade_id_trades", "events", "trades", ["trade_id"], ["trade_id"],
    )
    op.create_index("ix_events_trade_id", "events", ["trade_id"])


def downgrade() -> None:
    op.drop_index("ix_events_trade_id", table_name="events")
    op.drop_constraint("fk_events_trade_id_trades", "events", type_="foreignkey")
    op.drop_column("events", "trade_id")
