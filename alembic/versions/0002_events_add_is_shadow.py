"""events: add is_shadow column

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02

Phase 3 (shadow mode) gap: Event rows had no way to distinguish a
shadow-mode simulated event (e.g. TradeAttempt's internal "order_filled")
from a real broker event once Phase 4 adds real order placement -- unlike
Trade, which already had is_shadow from Phase 0. Mirrors that column
exactly. Every event this project emits before Phase 4 ships real order
code is_shadow=True; nothing sets it False yet, hence the server-side
default rather than requiring every call site to pass it explicitly.

VALID_EVENT_TYPES in app/models/event.py was also expanded in this same
change (swing confirmations, fvg_rejected_min_stop, trade_closed,
day_skipped_* / day_trend_determined, fomc_calendar_stale_warning) --
that's a Python-only, app-layer change (event_type has no DB-level CHECK
constraint, see the comment in event.py), so it needs no migration of its
own, but is called out here since it shipped alongside this one.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("is_shadow", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("events", "is_shadow")