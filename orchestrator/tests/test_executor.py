"""Agent runs as Jobs (task 7.7), against a fake cluster."""

import json
import threading

from orchestrator.executor import Executor, Finished, Run
from orchestrator.model import run_name
from orchestrator.queue import PostgresQueue


class FakeCluster:
    def __init__(self):
        self.jobs = {}  # name -> {"manifest", "task", "done"}

    def job_exists(self, name):
        return name in self.jobs

    def active_jobs(self):
        return sum(1 for j in self.jobs.values() if not j["done"])

    def create_run(self, run, manifest, task_json):
        self.creates = getattr(self, "creates", 0) + 1
        self.jobs.setdefault(run.name, {"manifest": manifest, "task": json.loads(task_json), "done": None})

    def finish(self, name, result):
        self.jobs[name]["done"] = result

    def delete_run(self, name):
        self.jobs.pop(name, None)

    def finished_jobs(self):
        self.listings = getattr(self, "listings", 0) + 1
        return [
            Finished(
                n,
                j["manifest"]["metadata"]["labels"]["sdlc/thread"],
                j["done"]["result"] != "failed",
                j["done"],
                uid=f"uid-{n}",
            )
            for n, j in self.jobs.items()
            if j["done"]
        ], "v1"

    def watch_finished(self, stop, since):
        """Two watches: the first reports a run that finishes (version v2), the second nothing."""
        self.watched_since = [*getattr(self, "watched_since", []), since]
        if len(self.watched_since) == 1:
            yield "v2", Finished("ewa-2-propose-1", "t-2", True, {"result": "spec_ready"}, uid="uid-x")
        else:
            stop.set()


def task(key, mode="propose", model="llm/deepseek/deepseek-v4-flash-0731"):
    return {
        "mode": mode,
        "repo": "sdlc/example-webapp",
        "base": "development",
        "branch": f"feat/{key}_x",
        "change": f"{key.lower()}-x",
        "model": model,
        "ticket": {"key": key, "title": "x", "text": ""},
    }


def executor(pool, cluster, image="gitea.sdlc.internal/platform/agent-runner:abc"):
    return Executor(cluster, pool, PostgresQueue(pool), image, "sdlc-agents", max_active=2)


def test_a_third_run_waits_until_one_finishes(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    names = [run_name(f"EWA-{n}", "propose", 1) for n in (1, 2, 3)]
    states = [ex.start(Run(name, f"t-{i}", task(f"EWA-{i + 1}"))) for i, name in enumerate(names)]
    assert states == ["started", "started", "waiting"] and names[2] not in cluster.jobs
    cluster.finish(names[0], {"result": "spec_ready"})
    ex.start_waiting()
    assert names[2] in cluster.jobs


def test_a_cancelled_run_neither_waits_nor_runs_and_its_room_goes_to_the_next(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    names = [run_name(f"EWA-{n}", "propose", 1) for n in (1, 2, 3, 4)]
    for i, name in enumerate(names):
        ex.start(Run(name, f"t-{i}", task(f"EWA-{i + 1}")))
    ex.cancel(names[3])  # waiting
    ex.cancel(names[0])  # going: EWA-3, waiting longest, takes its room
    assert set(cluster.jobs) == {names[1], names[2]}


def test_the_watcher_goes_on_from_the_last_version_and_lists_only_on_start(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    ex.start(Run("ewa-1-propose-1", "t-1", task("EWA-1")))
    cluster.finish("ewa-1-propose-1", {"result": "spec_ready"})
    ex.watch(threading.Event())
    assert cluster.listings == 1 and cluster.watched_since == ["v1", "v2"]
    with pool.connection() as conn:
        recorded = conn.execute("SELECT delivery_id FROM inbox WHERE source = 'job' ORDER BY id").fetchall()
    assert recorded == [("ewa-1-propose-1/uid-ewa-1-propose-1",), ("ewa-2-propose-1/uid-x",)], "each once"


def test_starting_a_run_twice_finds_the_first(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    run = Run("ewa-1-propose-1", "t-1", task("EWA-1"))
    assert [ex.start(run), ex.start(run)] == ["started", "started before"]
    assert len(cluster.jobs) == 1


def test_a_run_that_finished_while_the_orchestrator_was_down_is_picked_up(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    ex.start(Run("ewa-1-apply-1", "t-1", task("EWA-1", "apply")))
    cluster.finish("ewa-1-apply-1", {"result": "code_ready", "commit": "c0ffee"})
    restarted = executor(pool, cluster)  # a new process: it reads every finished Job again
    restarted.resync()
    restarted.resync()  # twice, as after two restarts
    seen = []
    queue = PostgresQueue(pool)
    while queue.process(lambda d: seen.append((d.source, d.delivery_id, d.routing_key, d.payload["result"]["result"]))):
        pass
    assert seen == [("job", "ewa-1-apply-1/uid-ewa-1-apply-1", "t-1", "code_ready")]


def test_a_changed_model_reaches_the_next_run_with_the_same_image(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    ex.start(Run("ewa-1-propose-1", "t-1", task("EWA-1")))
    ex.start(Run("ewa-2-propose-1", "t-2", task("EWA-2", model="llm/z-ai/glm-5.2")))
    images = {j["manifest"]["spec"]["template"]["spec"]["containers"][0]["image"] for j in cluster.jobs.values()}
    assert images == {"gitea.sdlc.internal/platform/agent-runner:abc"}
    assert cluster.jobs["ewa-2-propose-1"]["task"]["model"] == "llm/z-ai/glm-5.2"


def test_a_run_is_bounded_and_isolated(pool):
    cluster = FakeCluster()
    executor(pool, cluster).start(Run("ewa-1-propose-1", "t-1", task("EWA-1")))
    spec = cluster.jobs["ewa-1-propose-1"]["manifest"]["spec"]
    pod = spec["template"]["spec"]
    assert spec["activeDeadlineSeconds"] == 1800 and spec["backoffLimit"] == 0
    assert pod["automountServiceAccountToken"] is False and pod["securityContext"]["runAsNonRoot"]
    container = pod["containers"][0]["securityContext"]
    assert container["readOnlyRootFilesystem"] and container["capabilities"] == {"drop": ["ALL"]}
    assert spec["template"]["metadata"]["labels"]["sdlc/thread"] == "t-1"


def test_runs_start_oldest_first_and_none_jumps_the_queue(pool):
    cluster = FakeCluster()
    ex = executor(pool, cluster)
    for n in (1, 2, 3):
        ex.start(Run(f"ewa-{n}-propose-1", f"t-{n}", task(f"EWA-{n}")))
    cluster.finish("ewa-1-propose-1", {"result": "spec_ready"})
    assert ex.start(Run("ewa-4-propose-1", "t-4", task("EWA-4"))) == "waiting", "EWA-3 waited longer"
    assert "ewa-3-propose-1" in cluster.jobs and "ewa-4-propose-1" not in cluster.jobs


def test_a_result_without_a_thread_is_not_the_orchestrators(pool):
    ex = executor(pool, FakeCluster())
    ex.record(Finished("ewa-9-propose-1", "", True, {"result": "spec_ready"}, uid="u"))
    assert PostgresQueue(pool).pending() == 0


def test_a_reused_job_name_still_delivers_its_result(pool):
    ex = executor(pool, FakeCluster())
    ex.record(Finished("ewa-1-propose-1", "t-1", True, {"result": "spec_ready"}, uid="first"))
    ex.record(Finished("ewa-1-propose-1", "t-9", True, {"result": "spec_ready"}, uid="second"))
    assert PostgresQueue(pool).pending() == 2


def test_no_image_is_refused(pool):
    import pytest

    with pytest.raises(ValueError):
        Executor(FakeCluster(), pool, PostgresQueue(pool), "", "sdlc-agents")
