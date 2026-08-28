"""broker_credentials: add self-service provisioning fields

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-28

Phase 0 of self-service provisioning (see the plan this was built from).
Adds provisioning_status (+ CHECK constraint, same idiom as
ck_model_configs_status_valid) and the supporting
error/machine/claimed_at/account_label columns -- see
app/models/broker_credential.py for the full field-by-field reasoning.

Data migration: every row that already has a bridge_url set (today's
only account, provisioned by hand before this column existed) is
correctly backfilled to provisioning_status='active' rather than landing
on the raw column default -- it IS actively provisioned, that's just not
represented anywhere until this migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_credentials",
        sa.Column("provisioning_status", sa.String(), nullable=False, server_default="not_requested"),
    )
    op.add_column("broker_credentials", sa.Column("provisioning_error", sa.String(), nullable=True))
    op.add_column(
        "broker_credentials",
        sa.Column("provisioning_machine_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "broker_credentials", sa.Column("provisioning_claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("broker_credentials", sa.Column("provisioning_account_label", sa.String(), nullable=True))

    op.create_foreign_key(
        "fk_broker_credentials_provisioning_machine_id",
        "broker_credentials",
        "provisioning_machines",
        ["provisioning_machine_id"],
        ["machine_id"],
    )
    op.create_check_constraint(
        "ck_broker_credentials_provisioning_status_valid",
        "broker_credentials",
        "provisioning_status IN ('not_requested', 'pending', 'in_progress', 'active', 'failed')",
    )

    op.execute(
        """
        UPDATE broker_credentials
        SET provisioning_status = 'active'
        WHERE bridge_url IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_broker_credentials_provisioning_status_valid", "broker_credentials", type_="check")
    op.drop_constraint("fk_broker_credentials_provisioning_machine_id", "broker_credentials", type_="foreignkey")
    op.drop_column("broker_credentials", "provisioning_account_label")
    op.drop_column("broker_credentials", "provisioning_claimed_at")
    op.drop_column("broker_credentials", "provisioning_machine_id")
    op.drop_column("broker_credentials", "provisioning_error")
    op.drop_column("broker_credentials", "provisioning_status")
