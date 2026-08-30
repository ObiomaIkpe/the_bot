"""users: add is_admin

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-30

Replaces the separate, unscoped Streamlit admin_dashboard/ tool with a
real admin section inside the React frontend (app/routers/admin.py).
There is no self-service way to become an admin -- see
app/scripts/promote_to_admin.py, run by hand, same "no HTTP endpoint"
precedent as minting a provisioning machine token.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
