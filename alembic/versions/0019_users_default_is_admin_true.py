"""users: default is_admin to true, backfill existing users

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-02

Explicit policy change, not a bug fix -- confirmed with the user 2026-09-02
that "every user becomes an admin by default" really does mean every
user can see every OTHER user's trades/events/audit log (the admin
section is scoped across all users, not per-account), not just an
internal-tools flag. Supersedes 0016's original design ("no
self-service way to become an admin" -- see
app/scripts/promote_to_admin.py, also updated).
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET is_admin = true")
    op.alter_column("users", "is_admin", server_default="true")


def downgrade() -> None:
    op.alter_column("users", "is_admin", server_default="false")
    # Deliberately NOT reverting existing rows back to is_admin=false --
    # a schema downgrade shouldn't silently revoke access someone may
    # have come to rely on. Revoke specific accounts by hand via
    # `python -m app.scripts.promote_to_admin --email <e> --revoke` if
    # this policy is ever actually reversed.
