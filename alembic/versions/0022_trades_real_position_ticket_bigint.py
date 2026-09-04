"""trades: real_position_ticket becomes BigInteger

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-04

Real, serious bug found while building the orphan-recording fix
(2026-09-04): real_position_ticket was a plain 32-bit Postgres INTEGER
(max 2,147,483,647). Every real MT5 ticket actually observed this
session -- the original bug-1 ticket (3147397683), and both of this
session's orphans (3173996588, 3173996701) -- already exceeds that
limit. Confirmed live: `SELECT ... FROM trades WHERE
real_position_ticket IS NOT NULL` returns ZERO rows, despite this
account having placed real trades for weeks -- consistent with EVERY
real trade write having silently failed at the database level the
instant it tried to store a real ticket number this large, discarding
the entire row (simulated side included, since it's one INSERT), not
just the real-money columns.

Widens the column to BigInteger (8-byte, max ~9.2 quintillion) --
comfortably covers any real MT5 ticket number, current or future.
Confirmed via a full audit of every Integer column in the schema that
this is the ONLY one ever populated with a broker-assigned (not
application-chosen) number -- magic_number/max_concurrent_positions/
max_accounts are all small, app-controlled values, unaffected.

No data migration needed -- there is no existing data in this column
to convert; every row's real_position_ticket that should have held a
real value never successfully wrote one in the first place.
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("trades", "real_position_ticket", type_=sa.BigInteger())


def downgrade() -> None:
    op.alter_column("trades", "real_position_ticket", type_=sa.Integer())
