"""trades: add real_volume (broker-filled lot size)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-06

The dashboard data-consistency audit this session (see PENDING_ITEMS.md's
2026-09-06 section) surfaced that lot size / trade volume was never
persisted anywhere -- not on this table, not in any journaled
Event.details -- despite the bridge already returning it on every
position and deal (bridge/app/mt5_client.py's Position/Deal models both
carry "volume"). This column closes that gap.

Purely a real-broker concept -- nullable, same as every other real_*
column, with no simulated counterpart to mirror (the simulation works
in R-multiples, not lot sizes). Populated going forward by
OrderManager._on_fill()/get_real_outcome() (live fills),
write_orphan_trade() (orphan recovery), and
write_reconciled_historical_trade() (historical reconciliation) --
see shadow_runner/persistence.py. Existing rows are backfilled
separately by shadow_runner/scripts/backfill_real_volume_2026_09_06.py,
which queries the bridge's already-live /history/deals endpoint for
each existing real trade's entry deal.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("real_volume", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "real_volume")
