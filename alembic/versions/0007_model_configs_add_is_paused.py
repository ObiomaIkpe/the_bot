"""model_configs: add per-model is_paused

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-26

Decentralizes the pause control. Before this, UserSettings.is_paused was
the ONLY pause switch -- account-wide, stopping every one of a user's
models at once. That's a deliberate design (see order_manager.py's
on_trade_candidate_ready(): "this is 'stop everything for this user
right now'") and stays exactly as-is -- it remains the fast, guaranteed,
whole-account emergency stop.

This adds a SECOND, finer-grained layer on top: a per-model pause, for
stopping just one model without affecting any others the user runs.
Both are checked on every trade candidate (see order_manager.py); either
one being true blocks that model's real order placement.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "is_paused")
