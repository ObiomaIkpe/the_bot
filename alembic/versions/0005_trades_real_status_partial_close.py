"""trades: add real_status + partial-close columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-04

Phase 4 step 3 (overnight-position handling). A real trade no longer
necessarily resolves same-day (see runner.py's PositionTracker module
docstring for the full design): if still open at 5pm NY, half its
volume gets closed and the rest keeps running to natural resolution,
however many days that takes. This needs:

  - real_status: tracks a real trade's lifecycle independently of the
    existing `outcome` column (which describes the SIMULATION's
    same-day result and is set once, at day finalize time, and never
    revisited). real_status is null for shadow trades; 'open' ->
    'partial_closed' -> 'closed' for real ones, updated as the real
    position's actual lifecycle unfolds, potentially across many days
    after the trade row was first written.
  - partial_close_*: the 5pm partial-close leg's own price/time/volume/
    profit, kept separate from the final real_close_* columns (added in
    migration 0004) -- a partially-closed-then-fully-closed trade has
    TWO distinct real closing events, not one.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("real_status", sa.String(), nullable=True))
    op.add_column("trades", sa.Column("partial_close_price", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("partial_close_time_utc", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trades", sa.Column("partial_close_time_ny", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trades", sa.Column("partial_close_volume", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("partial_close_profit", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_trades_real_status_valid",
        "trades",
        "real_status IS NULL OR real_status IN ('open', 'partial_closed', 'closed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_trades_real_status_valid", "trades", type_="check")
    op.drop_column("trades", "partial_close_profit")
    op.drop_column("trades", "partial_close_volume")
    op.drop_column("trades", "partial_close_time_ny")
    op.drop_column("trades", "partial_close_time_utc")
    op.drop_column("trades", "partial_close_price")
    op.drop_column("trades", "real_status")