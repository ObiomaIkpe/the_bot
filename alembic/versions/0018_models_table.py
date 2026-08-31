"""models table -- dynamic model registry

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-31

Replaces the hardcoded model roster (ALL_MODEL_NAMES tuple in
app/models/model_config.py, plus separate hardcoded CHECK constraints
on events.model and trades.model) with a real `models` table --
adding a model is now "insert one row" (POST /admin/models), not a
migration. Seeds the 3 models that already exist (required before the
FK constraints below, or the existing rows in events/trades/
model_configs would violate them). See app/models/model.py's module
docstring for the full reasoning.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("model_name", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.execute(
        """
        INSERT INTO models (model_name, display_name) VALUES
            ('fvg', 'FVG'),
            ('ob', 'Order Block'),
            ('fvg_ob', 'FVG + Order Block')
        """
    )

    op.drop_constraint("ck_events_model_valid", "events", type_="check")
    op.drop_constraint("ck_trades_model_valid", "trades", type_="check")

    op.create_foreign_key("fk_events_model_models", "events", "models", ["model"], ["model_name"])
    op.create_foreign_key("fk_trades_model_models", "trades", "models", ["model"], ["model_name"])
    op.create_foreign_key(
        "fk_model_configs_model_name_models", "model_configs", "models", ["model_name"], ["model_name"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_model_configs_model_name_models", "model_configs", type_="foreignkey")
    op.drop_constraint("fk_trades_model_models", "trades", type_="foreignkey")
    op.drop_constraint("fk_events_model_models", "events", type_="foreignkey")

    op.create_check_constraint("ck_trades_model_valid", "trades", "model IN ('fvg', 'ob', 'fvg_ob')")
    op.create_check_constraint("ck_events_model_valid", "events", "model IN ('fvg', 'ob', 'fvg_ob')")

    op.drop_table("models")
