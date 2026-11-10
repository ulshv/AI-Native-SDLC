"""The orchestrator's own tables. LangGraph's checkpointer keeps the threads' state in its own."""

from __future__ import annotations

from psycopg_pool import ConnectionPool

SCHEMA = """
-- Every webhook and every finished agent run, recorded before anything acts on it (design
-- decision 4). The unique delivery id makes a second delivery a no-op. routing_key is the thread
-- when it is known, else the outside object, so the events of one thread run one at a time.
CREATE TABLE IF NOT EXISTS inbox (
    id           bigserial PRIMARY KEY,
    source       text NOT NULL,
    delivery_id  text NOT NULL,
    routing_key  text NOT NULL,
    headers      jsonb NOT NULL DEFAULT '{}',
    payload      jsonb NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    leased_until timestamptz,
    processed_at timestamptz,
    attempts     int NOT NULL DEFAULT 0,
    error        text,
    UNIQUE (source, delivery_id)
);
CREATE INDEX IF NOT EXISTS inbox_pending ON inbox (routing_key, id) WHERE processed_at IS NULL;
ALTER TABLE inbox ADD COLUMN IF NOT EXISTS leased_until timestamptz;

-- Which thread works on which outside object: a tracker work item, a pull request, later a chat
-- thread. An object belongs to one thread; a thread may have several objects.
CREATE TABLE IF NOT EXISTS thread_link (
    kind       text NOT NULL,
    ref        text NOT NULL,
    thread_id  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, ref)
);
CREATE INDEX IF NOT EXISTS thread_link_thread ON thread_link (thread_id);

-- Agent runs waiting for room: at most a few go at a time (design decision 7).
CREATE TABLE IF NOT EXISTS pending_runs (
    name       text PRIMARY KEY,
    thread_id  text NOT NULL,
    task       jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
"""

TABLES = ("inbox", "thread_link", "pending_runs", "checkpoints")


def connect(database_url: str, size: int = 8) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=1, max_size=size, kwargs={"autocommit": True}, open=True)
    pool.wait()
    return pool


def migrate(pool: ConnectionPool) -> None:
    """Creates or updates the orchestrator's tables (LangGraph's are agent.setup_checkpoints). Run
    by `orchestrator migrate`, a Job before every rollout; the service never changes the schema."""
    with pool.connection() as conn:
        conn.execute(SCHEMA)


def missing_tables(pool: ConnectionPool) -> list[str]:
    with pool.connection() as conn:
        return [t for t in TABLES if conn.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is None]
