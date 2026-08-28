"""provisioning_machines: new table for self-service MT5 provisioning

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-28

Phase 0 of self-service provisioning (see the plan this was built from).
A "machine" is a Windows VPS capable of running MT5 terminal copies +
bridge workers -- see app/models/provisioning_machine.py's own docstring
for why this is a real table rather than a hardcoded single value, even
though exactly one machine exists today.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provisioning_machines",
        sa.Column("machine_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("max_accounts", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("machine_token_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("label", name="uq_provisioning_machines_label"),
        sa.UniqueConstraint("machine_token_hash", name="uq_provisioning_machines_machine_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("provisioning_machines")
