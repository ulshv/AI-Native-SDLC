"""The wait for previews (task 9.3): rows in Postgres, the preview faked."""

from datetime import timedelta

from orchestrator.previews import Previews, preview_url, served_commit, version_url
from orchestrator.projects import Project
from orchestrator.queue import PostgresQueue


def events(pool):
    with pool.connection() as conn:
        rows = conn.execute("SELECT delivery_id, routing_key, payload FROM inbox ORDER BY id").fetchall()
    return [(d.rsplit("/", 1)[0], k, p) for d, k, p in rows]  # without the wait's start


def waits(pool):
    with pool.connection() as conn:
        return conn.execute("SELECT thread_id, commit FROM previews").fetchall()


def test_a_preview_serving_the_commit_is_told_to_its_thread_once(pool):
    serving = {}
    previews = Previews(pool, PostgresQueue(pool), fetch=lambda url: serving.get(url, ""))
    previews.watch("t1", "http://ewa-1.x-preview.svc/version.json", "b2")
    previews.check()
    assert events(pool) == [] and waits(pool) == [("t1", "b2")], "nothing served yet: it waits"
    serving["http://ewa-1.x-preview.svc/version.json"] = "a1"
    previews.check()
    assert events(pool) == [], "another commit does not count"
    serving["http://ewa-1.x-preview.svc/version.json"] = "b2"
    previews.check()
    previews.check()
    assert events(pool) == [("t1/b2", "t1", {"thread_id": "t1", "commit": "b2", "served": True})]
    assert waits(pool) == []


def test_watching_again_replaces_the_wait_and_a_wait_past_the_timeout_is_over(pool):
    previews = Previews(pool, PostgresQueue(pool), fetch=lambda url: "", timeout=timedelta(0))
    previews.watch("t1", "http://u", "b2")
    previews.watch("t1", "http://u", "c3")  # a fix: the new commit is waited for
    previews.check()
    assert events(pool) == [("t1/c3", "t1", {"thread_id": "t1", "commit": "c3", "served": False})]
    previews.watch("t2", "http://v", "d4")
    previews.forget("t2")
    previews.check()
    assert waits(pool) == [] and len(events(pool)) == 1


def test_the_preview_addresses_follow_the_release_and_the_project_domain():
    project = Project(
        "example-webapp",
        "sdlc",
        "EWA",
        "x",
        "sdlc/example-webapp",
        "development",
        "main",
        "example-webapp.sdlc.internal",
        True,
        (),
        (),
    )
    assert preview_url(project, "EWA-12") == "https://ewa-12.qa.example-webapp.sdlc.internal"
    assert version_url(project, "EWA-12") == "http://ewa-12.example-webapp-preview.svc/version.json"


def test_an_address_that_does_not_answer_serves_no_commit():
    assert served_commit("http://127.0.0.1:9/version.json") == ""
