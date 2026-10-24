"""multi-account-per-user, phase 2: backfill credential_id on existing rows

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-24

Data-only migration -- fills in the credential_id column migration 0024
added, for every row that already exists. Safe because the one-active-
per-user constraint still holds at this point (same pattern migration
0010's own backfill used): for any given user_id, there is still at
most one active broker_credentials row, so the match below is always
unambiguous. Rows with no owning user (user_id IS NULL -- shared
narrative events, the model's ownerless shadow trade row) are left with
credential_id NULL too, same as before -- there is no single account to
attribute them to.
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_BACKFILL_SQL = """
    UPDATE {table}
    SET credential_id = bc.credential_id
    FROM broker_credentials bc
    WHERE bc.user_id = {table}.user_id
      AND bc.is_active
      AND {table}.credential_id IS NULL
"""


def upgrade() -> None:
    op.execute(_BACKFILL_SQL.format(table="trades"))
    op.execute(_BACKFILL_SQL.format(table="events"))
    op.execute(_BACKFILL_SQL.format(table="model_configs"))


def downgrade() -> None:
    # Data-only migration -- nothing to undo. credential_id stays
    # populated on downgrade (harmless: migration 0024's column is still
    # present at this point in the downgrade chain, and nothing reads
    # this column yet regardless).
    pass
