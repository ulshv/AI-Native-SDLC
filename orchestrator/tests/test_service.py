"""The HTTP side and the entry point (tasks 7.1 and 7.9): webhooks reach the queue, migrations
are a command of their own, and the service starts against Postgres."""

import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from alembic.script import ScriptDirectory
from conftest import FIXTURES
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.api import SIGNATURE_HEADER, create_app
from orchestrator.links import Link, Links
from orchestrator.queue import PostgresQueue

SECRETS = {"plane": "plane-secret", "gitea": "gitea-secret"}
# What `serve` needs besides the database; none of it is called before a webhook comes.
SERVE_SECRETS = {
    "LLM_API_KEY": "test",
    "PLANE_API_KEY": "test",
    "GITEA_TOKEN": "test",
    "PLANE_WEBHOOK_SECRET": "test",
    "GITEA_WEBHOOK_SECRET": "test",
}


def app(pool, links=None):
    return TestClient(create_app(pool, PostgresQueue(pool), links or Links(pool), lambda thread: {}, SECRETS))


def post(client, source, name, delivery=None, secret=None):
    fixture = json.loads((FIXTURES / source / f"{name}.json").read_text())
    body = json.dumps(fixture["body"]).encode()
    headers = dict(fixture["headers"])
    if delivery:
        headers["X-Plane-Delivery" if source == "plane" else "X-Gitea-Delivery"] = delivery
    key = (secret if secret is not None else SECRETS.get(source, "")).encode()
    headers[SIGNATURE_HEADER.get(source, "X-Signature")] = hmac.new(key, body, hashlib.sha256).hexdigest()
    return client.post(f"/hooks/{source}", content=body, headers=headers)


def inbox(pool):
    with pool.connection() as conn:
        return conn.execute("SELECT source, routing_key FROM inbox ORDER BY id").fetchall()


def test_a_webhook_is_recorded_once_and_routed_by_its_object(pool):
    client = app(pool)
    assert post(client, "plane", "comment_mention_agent").json() == {"recorded": True}
    assert post(client, "plane", "comment_mention_agent").json() == {"recorded": False}
    assert inbox(pool) == [
        ("plane", "plane_work_item:72de36e4-09bb-4182-9176-097a5a93da7a/d71d47ba-8b66-45ce-bd4d-8cffb7a1c48a")
    ]


def test_a_webhook_about_a_linked_object_goes_to_its_thread(pool):
    links = Links(pool)
    links.link("t-1", Link("gitea_pr", "sdlc/example-webapp#3"))
    client = app(pool, links)
    post(client, "gitea", "review_approved")
    assert inbox(pool) == [("gitea", "t-1")]
    assert client.get("/threads", params={"link": "gitea_pr:sdlc/example-webapp#3"}).json() == {
        "thread_id": "t-1",
        "links": ["gitea_pr:sdlc/example-webapp#3"],
    }
    assert client.get("/threads", params={"link": "gitea_pr:sdlc/x#1"}).status_code == 404


def test_unknown_sources_and_bodies_that_are_not_json_are_refused(pool):
    client = app(pool)
    assert client.post("/hooks/jira", content="{}").status_code == 404
    signature = hmac.new(b"plane-secret", b"not json", hashlib.sha256).hexdigest()
    assert client.post("/hooks/plane", content="not json", headers={"X-Plane-Signature": signature}).status_code == 400
    assert client.get("/livez").json() == {"ok": True}


def test_a_webhook_without_its_signature_is_refused_and_not_recorded(pool):
    client = app(pool)
    assert post(client, "gitea", "review_approved", secret="guessed").status_code == 401
    assert client.post("/hooks/gitea", content=b"{}").status_code == 401  # no signature at all
    unsigned = TestClient(create_app(pool, PostgresQueue(pool), Links(pool), lambda thread: {}, {}))  # no secrets
    assert post(unsigned, "plane", "comment_mention_agent").status_code == 401
    assert inbox(pool) == []


def run(args, database_url, **kwargs):
    env = {**os.environ, "DATABASE_URL": database_url, "ORCHESTRATOR_CONFIG": "/nonexistent", **SERVE_SECRETS}
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.main", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        **kwargs,
    )


def test_migrate_reaches_the_head_revision_and_is_safe_to_repeat(empty_database_url):
    for _ in range(2):
        result = run(["migrate"], empty_database_url)
        assert result.returncode == 0, result.stderr
    assert db.schema(empty_database_url).fit is db.Fit.CURRENT and db.schema_problem(empty_database_url) is None


def test_a_database_a_newer_release_migrated_is_left_alone_and_runs_this_code(empty_database_url):
    run(["migrate"], empty_database_url)
    with db.connect(empty_database_url, size=1) as pool, pool.connection() as conn:
        conn.execute("UPDATE alembic_version SET version_num = '9999'")  # as after a rollback
    assert run(["migrate"], empty_database_url).returncode == 0
    assert db.schema(empty_database_url).fit is db.Fit.AHEAD and db.schema_problem(empty_database_url) is None


def test_a_database_behind_the_code_is_told_apart_from_one_ahead_of_it():
    script = ScriptDirectory.from_config(db._alembic(""))
    assert db.fit(script, "0001", "0002") is db.Fit.BEHIND  # a revision this code has, before its head
    assert db.fit(script, "9999", "0001") is db.Fit.AHEAD and db.fit(script, None, "0001") is db.Fit.EMPTY


def test_the_service_refuses_to_start_before_the_migration_starts_after_it_and_stops_cleanly(empty_database_url):
    refused = run(["serve", "--port", "1"], empty_database_url)
    assert refused.returncode == 1 and "orchestrator migrate" in refused.stderr
    run(["migrate"], empty_database_url)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "DATABASE_URL": empty_database_url,
        "ORCHESTRATOR_CONFIG": "/nonexistent",
        **SERVE_SECRETS,
        "PROJECTS_DIR": str(Path(__file__).parents[2] / "platform" / "projects"),
    }
    service = subprocess.Popen(
        [sys.executable, "-m", "orchestrator.main", "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=1).json() == {"ok": True}:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            service.terminate()
            raise AssertionError("the service did not answer /readyz: " + service.communicate(timeout=20)[1][-1500:])
        service.terminate()  # SIGTERM, as Kubernetes sends it
        stderr = service.communicate(timeout=60)[1]
        assert "workers stopped" in stderr, "the workers stop before the process ends: " + stderr[-1500:]
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(timeout=20)
