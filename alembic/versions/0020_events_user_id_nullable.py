"""events: user_id becomes nullable, for ownerless shared narrative rows

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-03

Multi-user fan-out, piece 1.5 (see MULTI_USER_FANOUT_PLAN.md's section
5, "Narrative event ownership"). Detection's shared narrative events
(raid/MSS/FVG/candidate/day-level -- see app.models.event's
NARRATIVE_EVENT_TYPES) no longer belong to any one person once
multiple subscribers can fan out from the same detected setup. Chosen
over attributing them to whichever account's bridge is serving as the
reference price feed, specifically because User.events cascades on
delete -- that alternative would mean deleting one account retroactively
destroys every subscriber's shared narrative history for a model, not
just going forward.

No data migration needed here: every existing row keeps its current
user_id untouched. Only new narrative-type rows written after
persistence.py's write_event() is updated (same commit) will ever be
NULL.
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("events", "user_id", nullable=True)


def downgrade() -> None:
    # Any row written NULL after this migration shipped would violate
    # the restored NOT NULL constraint -- if this is ever actually rolled
    # back, those rows need a real user_id backfilled by hand first
    # (there's no single correct owner to auto-assign).
    op.alter_column("events", "user_id", nullable=False)
