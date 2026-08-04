"""user_settings: replace live_model/shadow_models with model_configs

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04

Phase 4 design change (see this phase's chat history): every model runs
independently and manages its own trades -- there's no single "the live
model" anymore. This migration:

  1. Creates model_configs (one row per user+model: status, risk_pct,
     magic_number, max_concurrent_positions).
  2. Migrates EVERY existing user_settings row's live_model/shadow_models
     into model_configs rows -- real data migration, not just a schema
     change. The existing live_model becomes a 'shadow' row (NOT
     'active' -- the order-manager that actually places real orders per
     model doesn't exist in code yet as of this migration; promoting
     straight to 'active' here would be wrong regardless of whether
     anything bad could currently happen, since 'active' is meant to be
     a deliberate, separately-confirmed action, same philosophy as the
     bridge's orders_enabled kill switch). Each existing shadow_models
     entry becomes a 'disabled' row (no streaming pipeline exists yet
     for OB or FVG+OB -- 'shadow' would incorrectly imply one is
     actually running).
  3. Drops the now-obsolete live_model/shadow_models/
     max_concurrent_positions columns from user_settings
     (max_concurrent_positions moved to model_configs, nullable = no
     cap, matching the confirmed "infinite, per model" design).

Magic numbers assigned sequentially per user starting at 900001 (the
value already live on the VPS bridge's config.json for the existing FVG
worker -- this migration's first assigned number for the first migrated
user intentionally matches that, so nothing needs to change on the
bridge side for the already-tested FVG flow).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("config_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="disabled"),
        sa.Column("risk_pct", sa.Float(), nullable=False),
        sa.Column("magic_number", sa.Integer(), nullable=False, unique=True),
        sa.Column("max_concurrent_positions", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('disabled', 'shadow', 'active')", name="ck_model_configs_status_valid"),
        sa.UniqueConstraint("user_id", "model_name", name="uq_model_configs_user_model"),
    )
    op.create_index("ix_model_configs_user_id", "model_configs", ["user_id"])

    # ---- data migration: read existing live_model/shadow_models before
    # ---- the columns get dropped below ----
    bind = op.get_bind()
    existing_rows = bind.execute(
        sa.text("SELECT user_id, live_model, shadow_models, risk_pct FROM user_settings")
    ).fetchall()

    model_configs_table = sa.table(
        "model_configs",
        sa.column("config_id", postgresql.UUID(as_uuid=True)),
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("model_name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("risk_pct", sa.Float()),
        sa.column("magic_number", sa.Integer()),
    )

    next_magic = 900001
    for row in existing_rows:
        user_id, live_model, shadow_models, risk_pct = row
        if live_model:
            bind.execute(
                model_configs_table.insert().values(
                    config_id=uuid.uuid4(),
                    user_id=user_id,
                    model_name=live_model,
                    status="shadow",  # see module docstring -- deliberately not "active" yet
                    risk_pct=risk_pct,
                    magic_number=next_magic,
                )
            )
            next_magic += 1
        for shadow_model_name in (shadow_models or []):
            bind.execute(
                model_configs_table.insert().values(
                    config_id=uuid.uuid4(),
                    user_id=user_id,
                    model_name=shadow_model_name,
                    status="disabled",  # see module docstring -- no streaming pipeline exists yet
                    risk_pct=risk_pct,  # same starting risk_pct as the live model; adjust later per-model as needed
                    magic_number=next_magic,
                )
            )
            next_magic += 1

    # ---- now safe to drop the old columns ----
    op.drop_column("user_settings", "live_model")
    op.drop_column("user_settings", "shadow_models")
    op.drop_column("user_settings", "max_concurrent_positions")


def downgrade() -> None:
    op.add_column("user_settings", sa.Column("max_concurrent_positions", sa.Integer(), nullable=True))
    op.add_column("user_settings", sa.Column("shadow_models", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("user_settings", sa.Column("live_model", sa.String(), nullable=True))
    # Data is NOT restored on downgrade -- model_configs rows are simply
    # dropped. If you need the original live_model/shadow_models values
    # back, restore from a backup taken before running this migration.
    op.drop_index("ix_model_configs_user_id", table_name="model_configs")
    op.drop_table("model_configs")