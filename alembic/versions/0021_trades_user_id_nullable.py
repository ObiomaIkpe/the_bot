"""trades: user_id becomes nullable, for the ownerless shadow row

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-03

Multi-user fan-out, piece 2 (see MULTI_USER_FANOUT_PLAN.md section 5's
"Open questions, resolved" -- Trade.user_id decision). The model's own
simulated day (is_shadow=True, real_outcome=None) is written ALWAYS,
regardless of how many real subscribers exist -- this is what shadow-
mode model evaluation has always been (proving fvg itself before it
went live), previously piggybacking on one hardcoded account's user_id
purely because there was only ever one. Chosen ("option 1, proper")
over the alternative -- attributing it to the reference-feed account --
for the exact same reason as migration 0020's Event.user_id change:
User.trades cascades on delete, so that alternative would let deleting
one account destroy the model's entire shadow-mode trade AND equity
history, not just going forward.

Every per-subscriber real-outcome row keeps a real, NOT NULL-equivalent
user_id in practice (get_active_subscribers() only ever returns real
users) -- this migration doesn't change that, only makes NULL possible
for the one ownerless row per model per day.

No data migration needed: every existing row keeps its current user_id
untouched. Only new shadow rows written after runner.py's _write_trade()
is updated (same commit) will ever be NULL.
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("trades", "user_id", nullable=True)


def downgrade() -> None:
    # Same caveat as migration 0020's downgrade -- any row written NULL
    # after this shipped would violate the restored NOT NULL constraint;
    # a real rollback needs those rows handled by hand first (there's no
    # single correct owner to auto-assign to a shared shadow row).
    op.alter_column("trades", "user_id", nullable=False)
