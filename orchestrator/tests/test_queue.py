"""The queue and thread links (task 7.2, design decision 4)."""

import threading
import time

import pytest

from orchestrator.links import AlreadyLinked, Link, Links
from orchestrator.queue import PostgresQueue
from orchestrator.worker import Workers


def drain(queue, handler, limit=100):
    for _ in range(limit):
        if not queue.process(handler):
            return
    raise AssertionError("the queue did not drain")


def test_a_duplicate_delivery_takes_effect_once(pool):
    queue = PostgresQueue(pool)
    assert queue.put("plane", "d-1", "thread-a", {"n": 1})
    assert not queue.put("plane", "d-1", "thread-a", {"n": 1})
    seen = []
    drain(queue, lambda d: seen.append(d.delivery_id))
    assert seen == ["d-1"]


def test_events_of_one_thread_apply_one_at_a_time_in_arrival_order(pool):
    queue = PostgresQueue(pool)
    for n in range(6):
        queue.put("gitea", f"a-{n}", "thread-a", {"n": n})
        queue.put("gitea", f"b-{n}", "thread-b", {"n": n})
    order = {"thread-a": [], "thread-b": []}
    active, overlap, lock = {"thread-a": 0, "thread-b": 0}, [], threading.Lock()
    parallel = threading.Event()

    def handler(d):
        with lock:
            active[d.routing_key] += 1
            if active[d.routing_key] > 1:
                overlap.append(d.routing_key)
            if all(active.values()):
                parallel.set()
        time.sleep(0.05)
        with lock:
            order[d.routing_key].append(d.payload["n"])
            active[d.routing_key] -= 1

    workers = Workers(queue, handler, count=3, idle=0.02)
    workers.start()
    for _ in range(200):
        if queue.pending() == 0:
            break
        time.sleep(0.05)
    workers.stop()
    assert order == {"thread-a": list(range(6)), "thread-b": list(range(6))}
    assert overlap == []
    assert parallel.is_set(), "different threads should run in parallel"


def test_a_ticket_and_its_pull_request_reach_the_same_thread(pool):
    links = Links(pool)
    links.link("t-1", Link("plane_work_item", "p-uuid/w-uuid"))
    links.link("t-1", Link("gitea_pr", "sdlc/example-webapp#34"))
    assert links.thread_of(Link("plane_work_item", "p-uuid/w-uuid")) == "t-1"
    assert links.thread_of(Link("gitea_pr", "sdlc/example-webapp#34")) == "t-1"
    assert [str(link) for link in links.links_of("t-1")] == [
        "plane_work_item:p-uuid/w-uuid",
        "gitea_pr:sdlc/example-webapp#34",
    ]


def test_an_object_cannot_belong_to_two_threads(pool):
    links = Links(pool)
    links.link("t-1", Link("gitea_pr", "sdlc/example-webapp#34"))
    links.link("t-1", Link("gitea_pr", "sdlc/example-webapp#34"))  # again to the same thread: fine
    with pytest.raises(AlreadyLinked):
        links.link("t-2", Link("gitea_pr", "sdlc/example-webapp#34"))


def test_an_event_left_by_a_crashed_worker_is_handled_after_its_lease(pool):
    queue = PostgresQueue(pool)
    queue.put("plane", "d-1", "thread-a", {"n": 1})
    queue.put("plane", "d-2", "thread-a", {"n": 2})
    assert queue.take().delivery_id == "d-1"  # a worker took it and died
    assert not queue.process(lambda d: None), "neither it nor its thread's next event is taken while leased"
    with pool.connection() as conn:
        conn.execute("UPDATE inbox SET leased_until = now() - interval '1 second'")  # the lease ran out
    seen = []
    drain(queue, lambda d: seen.append(d.delivery_id))
    assert seen == ["d-1", "d-2"]


def test_an_event_that_kills_the_worker_every_time_is_closed(pool):
    queue = PostgresQueue(pool, max_attempts=2)
    queue.put("plane", "d-1", "thread-a", {})
    for _ in range(2):
        assert queue.take().delivery_id == "d-1"
        with pool.connection() as conn:
            conn.execute("UPDATE inbox SET leased_until = now() - interval '1 second'")
    assert queue.take() is None
    with pool.connection() as conn:
        error = conn.execute("SELECT error FROM inbox WHERE processed_at IS NOT NULL").fetchone()[0]
    assert "stopped while handling it, 2 times" in error


def test_rekey_while_the_event_is_being_handled_does_not_block(pool):
    queue = PostgresQueue(pool)
    queue.put("plane", "d-1", "plane_work_item:p/w", {})
    queue.put("plane", "d-2", "plane_work_item:p/w", {})
    seen = []

    def intake(d):
        if d.delivery_id == "d-1":
            queue.rekey("plane_work_item:p/w", "t-1")  # the handler links the item to a new thread
        seen.append((d.delivery_id, d.routing_key))

    drain(queue, intake)
    assert seen == [("d-1", "plane_work_item:p/w"), ("d-2", "t-1")]


def test_a_failing_event_is_retried_and_finally_closed_with_its_error(pool):
    queue = PostgresQueue(pool, max_attempts=2)
    queue.put("plane", "d-1", "thread-a", {})
    queue.put("plane", "d-2", "thread-a", {})

    def boom(d):
        if d.delivery_id == "d-1":
            raise RuntimeError("Plane is down")

    queue.process(boom)
    with pool.connection() as conn:
        conn.execute("UPDATE inbox SET available_at = now() - interval '1 second'")  # skip the backoff
    queue.process(boom)
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT delivery_id, attempts, processed_at IS NOT NULL, error FROM inbox ORDER BY id"
        ).fetchall()
    assert rows[0][:3] == ("d-1", 2, True) and "Plane is down" in rows[0][3]
    seen = []
    drain(queue, lambda d: seen.append(d.delivery_id))
    assert seen == ["d-2"], "the thread's next event is not stuck behind the failed one"


def test_waiting_events_move_to_the_thread_when_an_object_gets_one(pool):
    queue = PostgresQueue(pool)
    queue.put("plane", "d-1", "plane_work_item:p/w", {})
    queue.rekey("plane_work_item:p/w", "t-1")
    seen = []
    drain(queue, lambda d: seen.append(d.routing_key))
    assert seen == ["t-1"]
