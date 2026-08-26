"""user_settings: drop orphaned risk_pct column

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26

Migration 0003 moved risk_pct off user_settings and onto model_configs
(one row per user+model, since risk is no longer a single per-account
setting once multiple models can run independently -- see
model_config.py's module docstring). 0003 correctly copied every
existing user_settings.risk_pct value into a new model_configs row
before proceeding, but only dropped its three sibling columns
(live_model, shadow_models, max_concurrent_positions) -- it left this
one behind, still NOT NULL, with nothing in the current UserSettings
model declaring it. Net effect: no new user_settings row could be
created through the ORM as the code exists today (any insert violates
the NOT NULL constraint on a column the model doesn't know about) --
found while adding the admin API's GET /settings test coverage.

Purely a cleanup of already-orphaned data: risk_pct's real values live
in model_configs now, so nothing is lost by dropping this column.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("user_settings", "risk_pct")


def downgrade() -> None:
    # Historical per-account values are no longer recoverable (they were
    # already fanned out into model_configs by 0003) -- restored nullable
    # rather than re-imposing a NOT NULL constraint we can't correctly backfill.
    op.add_column("user_settings", sa.Column("risk_pct", sa.Float(), nullable=True))
