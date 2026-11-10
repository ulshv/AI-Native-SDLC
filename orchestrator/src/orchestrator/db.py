"""The orchestrator's database: its pool, and its schema as Alembic revisions (migrations/).
LangGraph's checkpointer keeps the threads' state in tables of its own (agent.setup_checkpoints).

Revisions are expand-then-contract, so the code of one release runs on the schema of the next:
during a rollout the old pods run on the new schema, and after a rollback to an older commit the
database stays where the newer one left it. Only a database behind the code stops it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from psycopg_pool import ConnectionPool
from sqlalchemy import create_engine, inspect, pool
from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(database_url: str, size: int = 8) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=1, max_size=size, kwargs={"autocommit": True}, open=True)
    pool.wait()
    return pool


def _alembic(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["database_url"] = database_url
    return config


class Fit(StrEnum):
    EMPTY = "empty"  # never migrated
    BEHIND = "behind"  # older than this code: migrate first
    CURRENT = "current"
    AHEAD = "ahead"  # migrated by a newer release, which this code runs on


@dataclass(frozen=True)
class Schema:
    revision: str | None  # the database's
    head: str  # this code's
    fit: Fit
    checkpoints: bool  # LangGraph's tables are there


def schema(database_url: str) -> Schema:
    """Where the database's schema stands against this code."""
    script = ScriptDirectory.from_config(_alembic(database_url))
    head = script.get_current_head()
    if head is None:
        raise LookupError(f"no Alembic revisions in {MIGRATIONS}")
    engine = create_engine(make_url(database_url).set(drivername="postgresql+psycopg"), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
            checkpoints = inspect(connection).has_table("checkpoints")
    finally:
        engine.dispose()
    return Schema(revision, head, fit(script, revision, head), checkpoints)


def fit(script: ScriptDirectory, revision: str | None, head: str) -> Fit:
    if revision is None:
        return Fit.EMPTY
    if revision == head:
        return Fit.CURRENT
    try:
        script.get_revision(revision)
    except CommandError:
        return Fit.AHEAD  # a revision this code does not have comes from a newer release
    return Fit.BEHIND


def migrate(database_url: str) -> None:
    """Brings the orchestrator's tables to this code's newest revision. Run by `orchestrator
    migrate`, a Job before every rollout; the service itself never changes the schema. A database
    a newer release migrated is left as it is."""
    if schema(database_url).fit is Fit.AHEAD:
        log.warning("the database is at a revision newer than this code's: left as it is")
        return
    command.upgrade(_alembic(database_url), "head")


def schema_problem(database_url: str) -> str | None:
    """Why this code cannot run on the database, or None when it can."""
    state = schema(database_url)
    if state.fit in (Fit.EMPTY, Fit.BEHIND):
        return (
            f"the database is at revision {state.revision or 'none'}, this code needs {state.head}: "
            "run `orchestrator migrate`"
        )
    if not state.checkpoints:
        return "LangGraph's checkpoint tables are missing: run `orchestrator migrate`"
    if state.fit is Fit.AHEAD:
        log.warning("the database is at revision %s, newer than this code's %s", state.revision, state.head)
    return None
