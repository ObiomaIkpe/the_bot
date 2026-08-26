"""broker_credentials: add bridge_fetch_token_hash

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-26

Part of eliminating the second, plaintext copy of MT5 credentials that
used to live in bridge/config.json. Instead of the bridge reading its
account's login/password/server from a local file, it fetches them once
at startup from a new endpoint (GET /internal/bridge-credentials),
authenticated by this token -- not the broker password itself, a
separate, narrow, rotatable capability.

Only the SHA-256 hash is ever stored (see app/core/security.py's new
service-token section) -- the plaintext token is shown to the user
exactly once, at mint time (POST /broker-credentials/{id}/bridge-token),
and never persisted or retrievable again. Unique + indexed since the
fetch endpoint looks a row up BY this hash (the token IS the lookup key,
not a separate id+secret pair).
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_credentials", sa.Column("bridge_fetch_token_hash", sa.String(), nullable=True))
    # A named UNIQUE CONSTRAINT, not an index -- matches what SQLAlchemy's
    # Column(unique=True) declares on the model side exactly (see
    # test_migrations.py's drift check: a unique index and a unique
    # constraint are different structures to Alembic's autogenerate diff,
    # even though Postgres implements both via a unique index under the
    # hood).
    op.create_unique_constraint(
        "uq_broker_credentials_bridge_fetch_token_hash",
        "broker_credentials",
        ["bridge_fetch_token_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_broker_credentials_bridge_fetch_token_hash", "broker_credentials", type_="unique")
    op.drop_column("broker_credentials", "bridge_fetch_token_hash")
