"""broker_credentials: add bridge_url

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-26

Each MT5 account maps to its own bridge worker process (its own port,
its own config.json -- see bridge/app/config.py's docstring: "Each
account gets its own process, its own port, and its own config file").
This column is how the api service knows WHICH bridge worker to talk to
for a given user's account, replacing the old assumption (a single
global BRIDGE_URL setting) that only ever worked for exactly one
account.

Nullable: submitting MT5 credentials (self-service, via the new
broker_credentials API) and having a bridge worker actually provisioned
for that account are two different events -- provisioning still
requires someone to install a real MT5 terminal on the Windows VPS and
start a worker for it (not something this migration or any API
endpoint can automate). A null bridge_url means "credentials saved, not
yet connected to a running worker" -- trading endpoints treat that the
same as "no broker credential configured" and return a clear 503.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_credentials", sa.Column("bridge_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("broker_credentials", "bridge_url")
