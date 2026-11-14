"""previews: the items in Deploying to QA, each waiting for its preview to serve a commit.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand only: the code of revision 0002 does not know the table, and still runs.
    op.create_table(
        "previews",
        sa.Column("thread_id", sa.Text, primary_key=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("commit", sa.Text, nullable=False),
        sa.Column("since", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only: fix forward with a new revision")
