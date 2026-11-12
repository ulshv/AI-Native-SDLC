"""The orchestrator's HTTP side (design decision 4): webhooks in, threads out.

A webhook whose signature holds is recorded in the queue and answered at once; the workers act on
it later. The event is routed by what it is about: the thread linked to that object, or the object
itself until it has a thread. The thread API follows LangGraph's Agent Protocol in shape
(threads, their state and messages).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg_pool import ConnectionPool

from .adapters import gitea, plane
from .links import Link, Links
from .metrics import registry
from .queue import Queue

log = logging.getLogger(__name__)

DELIVERY_HEADER = {"plane": "X-Plane-Delivery", "gitea": "X-Gitea-Delivery"}
# Both sign the body with HMAC-SHA256 and the webhook's secret, hex-encoded.
SIGNATURE_HEADER = {"plane": "X-Plane-Signature", "gitea": "X-Gitea-Signature"}
LINK_OF = {"plane": plane.link_of, "gitea": gitea.link_of}


def routing_key(links: Links, link: Link | None, source: str) -> str:
    if link is None:
        return f"{source}:unrouted"
    return links.thread_of(link) or str(link)


View = dict[str, Any]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def signed(secret: str, body: bytes, signature: str | None) -> bool:
    """Whether the signature is the body's under the secret; never without a secret."""
    if not secret or not signature:
        return False
    return hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), signature)


def create_app(
    pool: ConnectionPool,
    queue: Queue,
    links: Links,
    view_of: Callable[[str], View],
    secrets: Mapping[str, str],
    lifespan: Lifespan | None = None,
) -> FastAPI:
    """view_of(thread_id) returns what a thread shows ({"values": ..., "messages": ...}, as
    Threads.view), or {} for a thread without a work item."""
    app = FastAPI(title="orchestrator", docs_url=None, redoc_url=None, lifespan=lifespan)

    scraped = registry(pool)

    @app.get("/metrics")
    def metrics_page() -> Response:
        """Collected in a worker thread (a sync route): the reads it makes never hold up the event
        loop, so the probes and the webhooks are answered meanwhile."""
        return Response(generate_latest(scraped), media_type=CONTENT_TYPE_LATEST)

    @app.get("/livez")
    async def livez() -> dict[str, bool]:
        """The process answers; nothing outside it is asked, and on the event loop itself, not in the
        thread pool other routes may hold, so an outage elsewhere never gets the pod restarted."""
        return {"ok": True}

    @app.get("/readyz")
    def readyz() -> dict[str, bool]:
        """The process can serve: its database answers."""
        try:
            with pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
        except Exception as error:
            raise HTTPException(503, "the database does not answer") from error
        return {"ok": True}

    @app.post("/hooks/{source}")
    async def hook(source: str, request: Request) -> dict[str, bool]:
        if source not in LINK_OF:
            raise HTTPException(404, f"no webhooks from {source}")
        raw = await request.body()
        if not signed(secrets.get(source, ""), raw, request.headers.get(SIGNATURE_HEADER[source])):
            log.warning("a %s webhook without a valid signature: refused", source)
            raise HTTPException(401, "the signature is missing or wrong")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as error:
            raise HTTPException(400, "the body is not JSON") from error
        headers = {k: v for k, v in request.headers.items() if k.lower().startswith(("x-plane", "x-gitea"))}
        for name in ("x-plane-signature", "x-gitea-signature"):
            headers.pop(name, None)
        delivery = request.headers.get(DELIVERY_HEADER[source]) or hashlib.sha256(raw).hexdigest()
        link = LINK_OF[source](headers, body)

        def record() -> bool:  # the database calls block, so they leave the event loop
            return queue.put(source, delivery, routing_key(links, link, source), body, headers)

        return {"recorded": await run_in_threadpool(record)}

    @app.get("/threads")
    def find_thread(link: str) -> View:
        try:
            thread = links.thread_of(Link.parse(link))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if thread is None:
            raise HTTPException(404, f"no thread is linked to {link}")
        return {"thread_id": thread, "links": [str(other) for other in links.links_of(thread)]}

    def view_or_404(thread_id: str) -> View:
        view = view_of(thread_id)
        if not view and not links.links_of(thread_id):
            raise HTTPException(404, f"no thread {thread_id}")
        return view

    @app.get("/threads/{thread_id}")
    def thread(thread_id: str) -> View:
        return {
            "thread_id": thread_id,
            "values": view_or_404(thread_id).get("values", {}),
            "links": [str(other) for other in links.links_of(thread_id)],
        }

    @app.get("/threads/{thread_id}/messages")
    def messages(thread_id: str) -> View:
        return {"thread_id": thread_id, "messages": view_or_404(thread_id).get("messages", [])}

    return app
