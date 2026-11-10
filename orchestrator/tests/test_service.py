"""The HTTP side and the entry point (tasks 7.1 and 7.9): webhooks reach the queue, migrations
are a command of their own, and the service starts against Postgres."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from fastapi.testclient import TestClient

from conftest import FIXTURES
from orchestrator import db
from orchestrator.api import create_app
from orchestrator.links import Link, Links
from orchestrator.queue import PostgresQueue


def post(client, source, name, delivery=None):
    fixture = json.loads((FIXTURES / source / f"{name}.json").read_text())
    headers = dict(fixture["headers"])
    if delivery:
        headers["X-Plane-Delivery" if source == "plane" else "X-Gitea-Delivery"] = delivery
    return client.post(f"/hooks/{source}", content=json.dumps(fixture["body"]), headers=headers)


def inbox(pool):
    with pool.connection() as conn:
        return conn.execute("SELECT source, routing_key FROM inbox ORDER BY id").fetchall()


def test_a_webhook_is_recorded_once_and_routed_by_its_object(pool):
    client = TestClient(create_app(pool, PostgresQueue(pool), Links(pool)))
    assert post(client, "plane", "comment_mention_agent").json() == {"recorded": True}
    assert post(client, "plane", "comment_mention_agent").json() == {"recorded": False}
    assert inbox(pool) == [("plane", "plane_work_item:72de36e4-09bb-4182-9176-097a5a93da7a/d71d47ba-8b66-45ce-bd4d-8cffb7a1c48a")]


def test_a_webhook_about_a_linked_object_goes_to_its_thread(pool):
    links = Links(pool)
    links.link("t-1", Link("gitea_pr", "sdlc/example-webapp#3"))
    client = TestClient(create_app(pool, PostgresQueue(pool), links))
    post(client, "gitea", "review_approved")
    assert inbox(pool) == [("gitea", "t-1")]
    assert client.get("/threads", params={"link": "gitea_pr:sdlc/example-webapp#3"}).json() == \
        {"thread_id": "t-1", "links": ["gitea_pr:sdlc/example-webapp#3"]}
    assert client.get("/threads", params={"link": "gitea_pr:sdlc/x#1"}).status_code == 404


def test_unknown_sources_and_bodies_that_are_not_json_are_refused(pool):
    client = TestClient(create_app(pool, PostgresQueue(pool), Links(pool)))
    assert client.post("/hooks/jira", content="{}").status_code == 404
    assert client.post("/hooks/plane", content="not json").status_code == 400
    assert client.get("/healthz").json() == {"ok": True}


def run(args, database_url, **kwargs):
    env = {**os.environ, "DATABASE_URL": database_url, "ORCHESTRATOR_CONFIG": "/nonexistent", "LLM_API_KEY": "test"}
    return subprocess.run([sys.executable, "-m", "orchestrator.main", *args], env=env, capture_output=True,
                          text=True, timeout=120, **kwargs)


def test_migrate_is_a_command_and_safe_to_repeat(database_url):
    for _ in range(2):
        result = run(["migrate"], database_url)
        assert result.returncode == 0, result.stderr
    pool = db.connect(database_url, size=1)
    assert db.missing_tables(pool) == []
    pool.close()


def test_the_service_refuses_to_start_before_the_migration_and_starts_after_it(database_url):
    refused = run(["serve", "--port", "1"], database_url)
    assert refused.returncode == 1 and "orchestrator migrate" in refused.stderr
    run(["migrate"], database_url)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DATABASE_URL": database_url, "ORCHESTRATOR_CONFIG": "/nonexistent",
           "LLM_API_KEY": "test", "PROJECTS_DIR": str(Path(__file__).parents[2] / "platform" / "projects")}
    service = subprocess.Popen([sys.executable, "-m", "orchestrator.main", "serve", "--host", "127.0.0.1",
                                "--port", str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).json() == {"ok": True}:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            service.terminate()
            raise AssertionError("the service did not answer /healthz: " + service.communicate(timeout=20)[1][-1500:])
    finally:
        service.terminate()
        service.wait(timeout=20)
