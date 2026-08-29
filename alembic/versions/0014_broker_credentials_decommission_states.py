"""broker_credentials: add decommission (account removal) states

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-29

Account removal feature. Extends the existing provisioning_status /
provisioning_step state machine with a teardown half rather than adding
a new column -- see app/models/broker_credential.py's
VALID_PROVISIONING_STATUSES/VALID_PROVISIONING_STEPS docstrings for the
full reasoning. No data migration needed: no existing row can be in a
decommission-related state yet, since nothing could set one before this
migration + app/routers/internal_decommission.py exist.
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_broker_credentials_provisioning_status_valid", "broker_credentials", type_="check")
    op.create_check_constraint(
        "ck_broker_credentials_provisioning_status_valid",
        "broker_credentials",
        "provisioning_status IN ("
        "'not_requested', 'pending', 'in_progress', 'active', 'failed', "
        "'decommissioning', 'removing', 'removed', 'decommission_failed'"
        ")",
    )

    op.drop_constraint("ck_broker_credentials_provisioning_step_valid", "broker_credentials", type_="check")
    op.create_check_constraint(
        "ck_broker_credentials_provisioning_step_valid",
        "broker_credentials",
        "provisioning_step IS NULL OR provisioning_step IN ("
        "'cleaning_up', 'copying_terminal', 'launching_and_logging_in', 'verifying_login', "
        "'configuring_worker', 'installing_service', 'opening_firewall', 'waiting_for_health', "
        "'tearing_down'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_broker_credentials_provisioning_step_valid", "broker_credentials", type_="check")
    op.create_check_constraint(
        "ck_broker_credentials_provisioning_step_valid",
        "broker_credentials",
        "provisioning_step IS NULL OR provisioning_step IN ("
        "'cleaning_up', 'copying_terminal', 'launching_and_logging_in', 'verifying_login', "
        "'configuring_worker', 'installing_service', 'opening_firewall', 'waiting_for_health'"
        ")",
    )

    op.drop_constraint("ck_broker_credentials_provisioning_status_valid", "broker_credentials", type_="check")
    op.create_check_constraint(
        "ck_broker_credentials_provisioning_status_valid",
        "broker_credentials",
        "provisioning_status IN ('not_requested', 'pending', 'in_progress', 'active', 'failed')",
    )
