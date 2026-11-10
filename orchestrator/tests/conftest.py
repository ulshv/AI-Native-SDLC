"""Tests run against a real Postgres: the queue's locking and LangGraph's checkpointer need one.

ORCHESTRATOR_TEST_DSN points at any Postgres a test may create databases in. Without it, a
throwaway postgres pod starts in the namespace orchestrator-test of the platform's cluster,
reached through a port-forward, and is deleted when the tests end. Each test gets a database of
its own.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from orchestrator import db

FIXTURES = Path(__file__).parent / "fixtures"
IMAGE = "docker.io/library/postgres:18.6-alpine3.24"
NAMESPACE = "orchestrator-test"


os.environ.setdefault("KUBECONFIG", str(Path.home() / ".kube/config"))


def kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server_url():
    if os.environ.get("ORCHESTRATOR_TEST_DSN"):
        yield os.environ["ORCHESTRATOR_TEST_DSN"]
        return
    name = f"pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(["kubectl", "create", "namespace", NAMESPACE], capture_output=True)
    kubectl("run", name, "-n", NAMESPACE, f"--image={IMAGE}", "--restart=Never", "--port=5432",
            "--env=POSTGRES_PASSWORD=test", "--labels=app.kubernetes.io/part-of=orchestrator-tests")
    forward = None
    try:
        kubectl("wait", f"pod/{name}", "-n", NAMESPACE, "--for=condition=Ready", "--timeout=180s")
        # The pod is Ready before Postgres listens; a port-forward started too early dies.
        for _ in range(90):
            if subprocess.run(["kubectl", "exec", "-n", NAMESPACE, name, "--", "pg_isready", "-U", "postgres"],
                              capture_output=True).returncode == 0:
                break
            time.sleep(1)
        port = free_port()
        url = f"postgresql://postgres:test@127.0.0.1:{port}/postgres"
        for _ in range(30):
            if forward is None or forward.poll() is not None:
                forward = subprocess.Popen(["kubectl", "port-forward", "-n", NAMESPACE, f"pod/{name}", f"{port}:5432"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
            try:
                psycopg.connect(url, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                time.sleep(1)
        else:
            raise RuntimeError(f"cannot reach the test Postgres in pod {NAMESPACE}/{name}")
        yield url
    finally:
        if forward:
            forward.terminate()
        subprocess.run(["kubectl", "delete", "pod", name, "-n", NAMESPACE, "--wait=false"], capture_output=True)


@pytest.fixture
def database_url(server_url):
    """A fresh database for one test, dropped afterwards."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    yield server_url.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def pool(database_url):
    pool = db.connect(database_url)
    db.migrate(pool)
    yield pool
    pool.close()
