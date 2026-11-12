"""Operability (task 7.16): probes, metrics, a graceful stop and logs one can follow."""

import json
import logging
import threading

import httpx
import openai
from fastapi.testclient import TestClient

from orchestrator import db, logs, metrics
from orchestrator.api import create_app
from orchestrator.http import ApiError
from orchestrator.links import Links
from orchestrator.queue import PostgresQueue
from orchestrator.worker import Workers


def app(pool):
    return TestClient(create_app(pool, PostgresQueue(pool), Links(pool), lambda thread: {}, {}))


def test_a_database_outage_makes_the_pod_unready_but_not_dead(database_url):
    pool = db.connect(database_url, size=1)
    client = app(pool)
    assert client.get("/readyz").status_code == 200
    pool.close()  # the database is gone for this process
    assert client.get("/readyz").status_code == 503 and client.get("/livez").status_code == 200


def test_the_metrics_show_a_queued_event_and_every_target_from_zero(pool):
    PostgresQueue(pool).put("plane", "d-1", "thread-a", {})
    text = app(pool).get("/metrics").text
    assert "orchestrator_queue_depth 1.0" in text and "orchestrator_runs_waiting 0.0" in text
    assert 'orchestrator_external_errors_total{target="gitea"}' in text


def test_a_stop_releases_the_events_still_being_handled(pool):
    queue = PostgresQueue(pool)
    queue.put("plane", "d-1", "thread-a", {})
    started, hold = threading.Event(), threading.Event()

    def slow(delivery):
        started.set()
        hold.wait(10)

    workers = Workers(queue, slow, count=1)
    workers.start()
    assert started.wait(10)
    workers.stop(grace=0.2)
    taken = PostgresQueue(pool).take()  # the next pod, at once instead of after the lease
    hold.set()
    for thread in workers.threads:
        thread.join(10)
    assert taken is not None and taken.attempts == 2, "the stopped attempt still counts"


def test_a_log_line_is_json_with_the_ids_bound(capsys):
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    try:
        logs.configure()
        with logs.bound(delivery="plane/d-1", thread="t-1"):
            logging.getLogger("orchestrator.test").info("took %s", "it")
        line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    finally:
        root.handlers, root.level = handlers, level
    assert line["event"] == "took it" and line["delivery"] == "plane/d-1" and line["thread"] == "t-1"
    assert line["level"] == "info" and line["logger"] == "orchestrator.test"


def test_a_failed_call_is_counted_by_whom_it_went_to_and_the_codes_own_errors_are_not():
    def count(target):
        return metrics.EXTERNAL_ERRORS.labels(target)._value.get()

    before = {t: count(t) for t in ("gitea", "model")}
    metrics.failed_call(ApiError("POST", "/pulls", 403, "forbidden", "gitea"))
    metrics.failed_call(openai.APITimeoutError(request=httpx.Request("POST", "http://x")))
    metrics.failed_call(KeyError("phase"))
    assert count("gitea") == before["gitea"] + 1 and count("model") == before["model"] + 1
