"""broker_credentials: add provisioning_step for live progress reporting

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-29

Phase 2 of self-service provisioning. Adds provisioning_step (+ CHECK
constraint, same idiom as provisioning_status's own) -- see
app/models/broker_credential.py's VALID_PROVISIONING_STEPS for the full
reasoning. Nullable, no data migration needed: no row can meaningfully
have a "current step" until the new
POST /internal/provisioning-jobs/{id}/step endpoint starts being called,
which happens only for jobs claimed after this migration runs.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_credentials", sa.Column("provisioning_step", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_broker_credentials_provisioning_step_valid",
        "broker_credentials",
        "provisioning_step IS NULL OR provisioning_step IN ("
        "'cleaning_up', 'copying_terminal', 'launching_and_logging_in', 'verifying_login', "
        "'configuring_worker', 'installing_service', 'opening_firewall', 'waiting_for_health'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_broker_credentials_provisioning_step_valid", "broker_credentials", type_="check")
    op.drop_column("broker_credentials", "provisioning_step")
