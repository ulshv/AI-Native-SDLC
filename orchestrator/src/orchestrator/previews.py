"""Previews (design decision 11): the wait in Deploying to QA for a pull request's preview to
serve the commit the run pushed.

Argo CD deploys a preview of every pull request labelled preview, as the Helm release <id> in
<project>-preview, at <id>.qa.<domain>. Every image of a product serves /version.json with the
commit it was built from, so the watcher asks the preview's Service for it, inside the cluster,
needing neither Argo CD's API nor any RBAC. The waits are rows of `previews`, one per thread, so
a restart loses none. A preview that serves the commit, or a wait past TIMEOUT (CI failed or is
slow, and the pod waits for an image), becomes an event of its thread, unique per thread and
commit; then the row goes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from psycopg_pool import ConnectionPool

from .metrics import failed_call
from .projects import Project
from .queue import Queue

log = logging.getLogger(__name__)

POLL_SECONDS = 15
MINUTES = 15
TIMEOUT = timedelta(minutes=MINUTES)
FETCH_TIMEOUT = httpx.Timeout(5.0)


def preview_id(key: str) -> str:
    """The ticket's key in lower case (EWA-12 -> ewa-12), as the preview ApplicationSet takes it
    from the branch the item works on."""
    return key.lower()


def preview_url(project: Project, key: str) -> str:
    """The preview's address for people."""
    return f"https://{preview_id(key)}.qa.{project.domain}"


def version_url(project: Project, key: str) -> str:
    """The preview's /version.json through its Service, named after the Helm release."""
    return f"http://{preview_id(key)}.{project.name}-preview.svc/version.json"


def served_commit(url: str) -> str:
    """The commit the address serves; empty while it serves none (not deployed yet, no image yet)."""
    try:
        response = httpx.get(url, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        commit = response.json().get("commit")
        return commit if isinstance(commit, str) else ""
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""


def event(thread_id: str, commit: str, served: bool, since: str) -> tuple[str, dict[str, Any]]:
    """A wait's outcome as an event of its thread: its delivery id, unique per wait, and payload."""
    return f"{thread_id}/{commit}/{since}", {"thread_id": thread_id, "commit": commit, "served": served}


class Previews:
    """The PreviewWatch port, and the watcher that keeps it."""

    def __init__(
        self,
        pool: ConnectionPool,
        queue: Queue,
        fetch: Callable[[str], str] = served_commit,
        timeout: timedelta = TIMEOUT,
    ):
        self.pool, self.queue, self.fetch, self.timeout = pool, queue, fetch, timeout

    def watch(self, thread_id: str, url: str, commit: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO previews (thread_id, url, commit) VALUES (%s, %s, %s)
                   ON CONFLICT (thread_id) DO UPDATE SET url = excluded.url, commit = excluded.commit,
                   since = now()""",
                (thread_id, url, commit),
            )
        log.info("waiting for %s to serve %s", url, commit[:12])

    def forget(self, thread_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM previews WHERE thread_id = %s", (thread_id,))

    def check(self) -> None:
        """One round: every wait whose preview serves its commit, or that is over, is told to its
        thread. The event comes first, so a round that stops halfway repeats it, and the queue
        records it once."""
        with self.pool.connection() as conn:
            waits = conn.execute(
                """SELECT thread_id, url, commit, now() - since > %s, extract(epoch FROM since)::bigint::text
                   FROM previews ORDER BY since""",
                (self.timeout,),
            ).fetchall()
        for thread_id, url, commit, over, since in waits:
            served = self.fetch(url) == commit
            if not (served or over):
                continue
            delivery_id, payload = event(thread_id, commit, served, since)
            self.queue.put("preview", delivery_id, thread_id, payload)
            with self.pool.connection() as conn:
                conn.execute("DELETE FROM previews WHERE thread_id = %s AND commit = %s", (thread_id, commit))
            log.info("%s %s %s", url, "serves" if served else "did not serve in time", commit[:12])

    def run(self, stop: threading.Event) -> None:
        """Runs in its own thread until stop is set."""
        while not stop.wait(POLL_SECONDS):
            try:
                self.check()
            except Exception as error:
                if stop.is_set():
                    return  # shutting down: the pool may be closed already
                failed_call(error)
                log.exception("checking previews failed")
