"""The command a delivery was read as, kept with it so every retry acts on the same reading.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: the code of revision 0001 neither reads nor writes the column, and still runs.
    op.add_column("inbox", sa.Column("command", JSONB))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only: fix forward with a new revision")
