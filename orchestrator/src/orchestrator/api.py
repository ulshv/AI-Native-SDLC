"""The orchestrator's HTTP side (design decision 4): webhooks in, threads out.

A webhook is recorded in the queue and answered at once; the workers act on it later. The event
is routed by what it is about: the thread linked to that object, or the object itself until it
has a thread. The thread API follows LangGraph's Agent Protocol in shape (threads, their state
and messages); it comes with task 7.9.
"""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from psycopg_pool import ConnectionPool

from .adapters import gitea, plane
from .links import Link, Links
from .queue import Queue

log = logging.getLogger(__name__)

DELIVERY_HEADER = {"plane": "X-Plane-Delivery", "gitea": "X-Gitea-Delivery"}
LINK_OF = {"plane": plane.link_of, "gitea": gitea.link_of}


def routing_key(links: Links, link: Link | None, source: str) -> str:
    if link is None:
        return f"{source}:unrouted"
    return links.thread_of(link) or str(link)


def create_app(pool: ConnectionPool, queue: Queue, links: Links) -> FastAPI:
    app = FastAPI(title="orchestrator", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}

    @app.post("/hooks/{source}")
    async def hook(source: str, request: Request):
        if source not in LINK_OF:
            raise HTTPException(404, f"no webhooks from {source}")
        raw = await request.body()
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
    def find_thread(link: str):
        try:
            thread = links.thread_of(Link.parse(link))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if thread is None:
            raise HTTPException(404, f"no thread is linked to {link}")
        return {"thread_id": thread, "links": [str(l) for l in links.links_of(thread)]}

    return app
