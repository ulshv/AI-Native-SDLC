"""The orchestrator's tables: the inbox, thread links and pending runs.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    # Every webhook and every finished agent run, recorded before anything acts on it (design
    # decision 4). The unique delivery id makes a second delivery a no-op. routing_key is the
    # thread when it is known, else the outside object, so one thread's events run one at a time.
    op.create_table(
        "inbox",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("delivery_id", sa.Text, nullable=False),
        sa.Column("routing_key", sa.Text, nullable=False),
        sa.Column("headers", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("available_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("leased_until", sa.TIMESTAMP(timezone=True)),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.UniqueConstraint("source", "delivery_id"),
    )
    op.create_index("inbox_pending", "inbox", ["routing_key", "id"], postgresql_where=sa.text("processed_at IS NULL"))

    # Which thread works on which outside object: a tracker work item, a pull request, later a
    # chat thread. An object belongs to one thread; a thread may have several objects.
    op.create_table(
        "thread_link",
        sa.Column("kind", sa.Text, primary_key=True),
        sa.Column("ref", sa.Text, primary_key=True),
        sa.Column("thread_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("thread_link_thread", "thread_link", ["thread_id"])

    # Agent runs waiting for room: at most a few go at a time (design decision 7).
    op.create_table(
        "pending_runs",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("thread_id", sa.Text, nullable=False),
        sa.Column("task", JSONB, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
    )


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only: fix forward with a new revision")
