"""multi-account-per-user, phase 1: add credential_id to trades/events/model_configs

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-24

Purely additive -- nullable columns, no backfill, no constraint changes,
no application code changes in this migration. Each of trades, events,
and model_configs gets a credential_id FK to broker_credentials, so a
future phase can scope them to a specific broker account instead of
assuming "the one active account per user" everywhere. See the
multi-account-per-user plan (misty-seeking-crescent.md) for the full
phased approach -- this is phase 1 of 6. Migration 0025 backfills
existing rows; nothing reads or writes this column yet.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_credentials.credential_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_trades_credential_id", "trades", ["credential_id"])

    op.add_column(
        "events",
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_credentials.credential_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_events_credential_id", "events", ["credential_id"])

    op.add_column(
        "model_configs",
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("broker_credentials.credential_id"),
            nullable=True,
        ),
    )
    op.create_index("ix_model_configs_credential_id", "model_configs", ["credential_id"])


def downgrade() -> None:
    op.drop_index("ix_model_configs_credential_id", table_name="model_configs")
    op.drop_column("model_configs", "credential_id")

    op.drop_index("ix_events_credential_id", table_name="events")
    op.drop_column("events", "credential_id")

    op.drop_index("ix_trades_credential_id", table_name="trades")
    op.drop_column("trades", "credential_id")
