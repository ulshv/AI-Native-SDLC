"""Alembic's environment for the orchestrator's own tables; LangGraph's checkpointer keeps its
own (agent.setup_checkpoints). The database comes from `orchestrator migrate` (db.migrate) or,
for the alembic CLI, from DATABASE_URL."""

import os

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported: run them against the database")

url = context.config.attributes.get("database_url") or os.environ["DATABASE_URL"]
engine = create_engine(make_url(url).set(drivername="postgresql+psycopg"), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
