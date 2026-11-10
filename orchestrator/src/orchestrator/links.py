"""thread_link: outside objects and the one thread each belongs to."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import ConnectionPool


class AlreadyLinked(Exception):
    """The object belongs to another thread already."""


@dataclass(frozen=True)
class Link:
    kind: str
    ref: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.ref}"

    @classmethod
    def parse(cls, text: str) -> Link:
        kind, _, ref = text.partition(":")
        if not kind or not ref:
            raise ValueError(f"not a link: {text!r}")
        return cls(kind, ref)


class Links:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def thread_of(self, link: Link) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT thread_id FROM thread_link WHERE kind = %s AND ref = %s", (link.kind, link.ref)
            ).fetchone()
        return row[0] if row else None

    def link(self, thread_id: str, link: Link) -> None:
        """Links the object to the thread. Linking it again to the same thread is fine."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO thread_link (kind, ref, thread_id) VALUES (%s, %s, %s)
                   ON CONFLICT (kind, ref) DO UPDATE SET kind = EXCLUDED.kind
                   RETURNING thread_id""",
                (link.kind, link.ref, thread_id),
            ).fetchone()
        owner = row[0] if row else thread_id  # RETURNING always gives the row: inserted or kept
        if owner != thread_id:
            raise AlreadyLinked(f"{link} belongs to thread {owner}")

    def links_of(self, thread_id: str) -> list[Link]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, ref FROM thread_link WHERE thread_id = %s ORDER BY created_at", (thread_id,)
            ).fetchall()
        return [Link(kind, ref) for kind, ref in rows]
