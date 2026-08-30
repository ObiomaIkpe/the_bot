"""add audit_log table for security/identity events

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-30

Logging/audit review (this session) found the trading pipeline has
excellent audit coverage via `events`, but everything auth- and
credential-lifecycle-adjacent had NO durable audit trail at all:
register/login/password-change (app/routers/auth.py) only logged to
stdout; broker credential create/update/remove and bridge-token
issuance/rotation (app/routers/broker_credentials.py) had zero logging;
provisioning/decommission job claim/complete/fail
(app/routers/internal_provisioning.py, internal_decommission.py) had
zero logging; and app/routers/internal_bridge.py's get_bridge_credentials
-- which returns a DECRYPTED MT5 login/password/server to a bridge
worker, the single most security-sensitive endpoint in the system --
left zero trace of who/what fetched a live account's plaintext
credentials, success or failure.

This is a new table, not an extension of `events`: `events.model` is
CHECK-constrained to fvg/ob/fvg_ob and `is_shadow` is meaningless
outside the trading pipeline, and this needs a genuinely different,
polymorphic actor model (JWT user / machine-token ProvisioningMachine /
bridge-token BrokerCredential) that `events`' single `user_id` column
can't express. See app/models/audit_log.py for the full column
rationale and VALID_AUDIT_EVENT_TYPES/VALID_ACTOR_TYPES.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('user', 'machine', 'credential', 'unknown')",
            name="ck_audit_log_actor_type_valid",
        ),
    )
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_type", "actor_id"])
    op.create_index("ix_audit_log_resource", "audit_log", ["resource_type", "resource_id"])
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_timestamp", table_name="audit_log")
    op.drop_index("ix_audit_log_resource", table_name="audit_log")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_table("audit_log")
