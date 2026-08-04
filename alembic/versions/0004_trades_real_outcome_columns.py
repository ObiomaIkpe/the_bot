"""trades: add real_* columns for real-vs-simulated reconciliation

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-04

Phase 4 step 3 (part 2). Adds columns holding the REAL broker-side
outcome of a trade (fill price, fill time, close price, close time,
profit, close reason) alongside the existing columns, which have always
held the SIMULATION's view (DayOrchestrator/TradeAttempt's computed
entry/exit/outcome). Nothing about the existing columns changes -- this
is purely additive.

All nine new columns are nullable: the overwhelming majority of trade
rows are still shadow-mode (is_shadow=True) and will never populate
these; even a real (is_shadow=False) trade only populates them once
OrderManager's real fill/close detection has actually run (see
shadow_runner/order_manager.py's get_real_outcome()) -- there is
necessarily a gap between a trade row being written (at simulation
finalize time) and the real columns being known.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("real_position_ticket", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("real_fill_price", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("real_fill_time_utc", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trades", sa.Column("real_fill_time_ny", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trades", sa.Column("real_close_price", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("real_close_time_utc", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trades", sa.Column("real_close_time_ny", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trades", sa.Column("real_profit", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("real_close_reason", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "real_close_reason")
    op.drop_column("trades", "real_profit")
    op.drop_column("trades", "real_close_time_ny")
    op.drop_column("trades", "real_close_time_utc")
    op.drop_column("trades", "real_close_price")
    op.drop_column("trades", "real_fill_time_ny")
    op.drop_column("trades", "real_fill_time_utc")
    op.drop_column("trades", "real_fill_price")
    op.drop_column("trades", "real_position_ticket")