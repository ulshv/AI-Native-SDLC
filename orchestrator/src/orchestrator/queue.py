"""The event queue (design decision 4): an interface, with a table in Postgres behind it.

Recording an event and acting on it are apart: a webhook is answered once the event is in the
table, and workers take events one by one. Events with the same routing key (a thread, or the
outside object before it has one) are taken in the order they arrived, never two at a time;
different keys run in parallel.

A worker takes an event with a lease: a short transaction counts the attempt and marks the row
leased, and no lock is held while the handler runs. The key's later events wait while it is
leased. When the handler returns, the event is done; when it raises, the event waits with a
growing backoff; when the process dies, the lease runs out and the event is taken again. After
max_attempts it is closed with its error, so the thread's later events are not stuck behind it.
Effects are at least once: the handler makes an event that ran before harmless (the graph keeps
the deliveries it applied). Another implementation, such as NATS when several services consume
events, only has to keep this interface.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .logs import bound
from .metrics import GIVEN_UP, failed_call

log = logging.getLogger(__name__)

# Longer than any handler takes: a step makes a few calls, each bounded by http.CALL_BUDGET and
# llm.TIMEOUT with its retries.
LEASE_SECONDS = 900


@dataclass(frozen=True)
class Delivery:
    id: int
    source: str
    delivery_id: str
    routing_key: str
    headers: dict[str, str]
    payload: dict[str, Any]
    attempts: int


Handler = Callable[[Delivery], None]


class Queue(Protocol):
    def put(
        self,
        source: str,
        delivery_id: str,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        """Records an event; False when this delivery was recorded before."""
        ...

    def process(self, handler: Handler) -> bool:
        """Runs the handler on the next event that may run; False when there is none."""
        ...

    def rekey(self, old: str, new: str) -> None:
        """Moves the waiting events of a key to another one (an object that got a thread)."""
        ...

    def release_unfinished(self) -> int:
        """Gives the events this process is still handling back, as if never taken, for another
        process to take at once instead of after their lease; returns how many."""
        ...


# The oldest waiting event whose key has no older unfinished event; a leased one is unfinished.
TAKE = """
UPDATE inbox SET attempts = attempts + 1, leased_until = clock_timestamp() + make_interval(secs => %(lease)s)
WHERE id = (
    SELECT id FROM inbox AS i
    WHERE processed_at IS NULL AND available_at <= clock_timestamp()
      AND (leased_until IS NULL OR leased_until < clock_timestamp())
      AND NOT EXISTS (SELECT 1 FROM inbox AS o
                      WHERE o.processed_at IS NULL AND o.routing_key = i.routing_key AND o.id < i.id)
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING id, source, delivery_id, routing_key, headers, payload, attempts
"""


class PostgresQueue:
    def __init__(self, pool: ConnectionPool, max_attempts: int = 5, lease: int = LEASE_SECONDS):
        self.pool, self.max_attempts, self.lease = pool, max_attempts, lease
        self._handling: set[int] = set()  # the events this process has taken and not finished
        self._lock = threading.Lock()

    def put(
        self,
        source: str,
        delivery_id: str,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bool:
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO inbox (source, delivery_id, routing_key, headers, payload)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (source, delivery_id) DO NOTHING RETURNING id""",
                (source, delivery_id, routing_key, Jsonb(headers or {}), Jsonb(payload)),
            ).fetchone()
        if row is None:
            log.info("duplicate delivery %s/%s, recorded before", source, delivery_id)
        return row is not None

    def take(self) -> Delivery | None:
        with self.pool.connection() as conn:
            # An event whose every attempt ended with the worker gone is closed, not taken again.
            closed = conn.execute(
                """UPDATE inbox SET processed_at = clock_timestamp(), leased_until = NULL,
                     error = 'the worker stopped while handling it, ' || attempts || ' times'
                   WHERE processed_at IS NULL AND attempts >= %s AND leased_until < clock_timestamp()""",
                (self.max_attempts,),
            ).rowcount
            row = conn.execute(TAKE, {"lease": self.lease}).fetchone()
        if closed:
            GIVEN_UP.inc(closed)
        return Delivery(*row) if row else None

    def process(self, handler: Handler) -> bool:
        delivery = self.take()
        if delivery is None:
            return False
        with self._lock:
            self._handling.add(delivery.id)
        # Every line logged meanwhile, the failure's included, names the event and its thread (or,
        # before the item has one, the object it is about).
        with bound(delivery=f"{delivery.source}/{delivery.delivery_id}", thread=delivery.routing_key):
            try:
                self._handle(handler, delivery)
            finally:
                with self._lock:
                    self._handling.discard(delivery.id)
        return True

    def _handle(self, handler: Handler, delivery: Delivery) -> None:
        try:
            handler(delivery)
        except Exception as error:
            self.failed(delivery, error)
            return
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE inbox SET processed_at = clock_timestamp(), leased_until = NULL, error = NULL WHERE id = %s",
                (delivery.id,),
            )

    def release_unfinished(self) -> int:
        with self._lock:
            ids = list(self._handling)
        if not ids:
            return 0
        # The attempt still counts: an event in flight at every stop is given up in the end. The
        # process ends right after this, so its handler does not finish beside the next pod's.
        with self.pool.connection() as conn:
            released = conn.execute(
                "UPDATE inbox SET leased_until = NULL WHERE id = ANY(%s) AND processed_at IS NULL", (ids,)
            ).rowcount
        log.info("released %d events still being handled", released)
        return released

    def failed(self, delivery: Delivery, error: Exception) -> None:
        final = delivery.attempts >= self.max_attempts
        log.exception(
            "event %s failed, attempt %d%s",
            delivery.id,
            delivery.attempts,
            ", giving up" if final else "",
            exc_info=error,
        )
        failed_call(error)
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE inbox SET error = %s, leased_until = NULL,
                     available_at = clock_timestamp() + make_interval(secs => %s),
                     processed_at = CASE WHEN %s THEN clock_timestamp() END
                   WHERE id = %s""",
                (
                    f"{type(error).__name__}: {error}"[:2000],
                    min(2**delivery.attempts, 300),
                    final,
                    delivery.id,
                ),
            )
        if final:
            GIVEN_UP.inc()

    def rekey(self, old: str, new: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE inbox SET routing_key = %s WHERE routing_key = %s AND processed_at IS NULL", (new, old)
            )

    def pending(self) -> int:
        with self.pool.connection() as conn:
            (count,) = conn.execute("SELECT count(*) FROM inbox WHERE processed_at IS NULL").fetchone() or (0,)
        return int(count)
