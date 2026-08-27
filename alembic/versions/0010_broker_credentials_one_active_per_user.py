"""broker_credentials: enforce at most one active credential per user

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-27

create_broker_credential() never set is_active explicitly, so every new
credential landed on the column's own default (True) -- connecting a
second MT5 account left two active rows for the same user at once, and
get_bridge_client() (app/routers/trading.py) does a plain
.filter(is_active=True).first() with no ORDER BY, so which account
actually got used was arbitrary and silent. Application code
(app/routers/broker_credentials.py) now deactivates any other active
credential before activating one; this migration adds the DB-level
backstop so that invariant holds even if some future code path forgets
to do that.

Data cleanup runs first and is defensive, not corrective of a known
problem: no account is currently known to violate this, but
create_index below would fail outright if one did. There's no
created_at column to prefer "most recently connected," so the tiebreak
(lowest credential_id) is arbitrary but deterministic -- a gap worth
knowing about, not fixed here.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE broker_credentials
        SET is_active = false
        WHERE is_active = true
          AND credential_id NOT IN (
              SELECT DISTINCT ON (user_id) credential_id
              FROM broker_credentials
              WHERE is_active = true
              ORDER BY user_id, credential_id
          )
        """
    )
    op.create_index(
        "uq_broker_credentials_one_active_per_user",
        "broker_credentials",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    # Only undoes the index -- any rows the upgrade's data cleanup
    # deactivated stay deactivated (which one "should" be active again
    # isn't recoverable information).
    op.drop_index("uq_broker_credentials_one_active_per_user", table_name="broker_credentials")
